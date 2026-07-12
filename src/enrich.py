"""Derived mathematics for feed cards - computed from the database, never by the LLM.

Everything here is a deterministic aggregate over the local catalog. The card
generator hands these numbers to the LLM with the rule "you may only use numbers
from this section", which is the anti-hallucination contract of the feed.

    from src.enrich import enrich
    enrich("TSLA", "equities")
    -> {"stats": {...}, "formulas": [...]}
"""

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from src import dataset as ds

# Human-friendly names for the normalized category levels, per asset class
LEVEL_LABELS = {
    "equities": ("sector", "industry group", "industry"),
    "etfs": ("category group", "category", "fund family"),
    "funds": ("category group", "category", "fund family"),
    "indices": ("category group", "category", None),
    "currencies": ("base currency", "quote currency", None),
    "cryptos": ("base coin", None, None),
    "moneymarkets": (None, None, "family"),
}


@lru_cache(maxsize=1)
def _tables() -> dict:
    """One-time aggregates over the catalog (~1s, cached for process life)."""
    c = ds.catalog()
    sizes = {}
    for level in ("category_l1", "category_l2", "category_l3"):
        sizes[level] = c.groupby(["asset_class", level]).size()
    summary_sorted = {
        cls: np.sort(grp.loc[grp["has_summary"], "summary_len"].to_numpy())
        for cls, grp in c.groupby("asset_class")
    }
    return {
        "catalog": c.set_index(["asset_class", "symbol"]),
        "class_total": c.groupby("asset_class").size(),
        "level_sizes": sizes,
        "country": c.groupby(["asset_class", "country"]).size(),
        "country_l3": c.groupby(["asset_class", "country", "category_l3"]).size(),
        "name_listings": c.groupby(["asset_class", "name"]).size(),
        "delisted_l2": c.groupby(["asset_class", "category_l2"])["delisted"].mean(),
        "cap_l3": c.groupby(["asset_class", "category_l3", "market_cap"]).size(),
        "exchange": c.groupby(["asset_class", "exchange"]).size(),
        "summary_sorted": summary_sorted,
    }


def _get(series: pd.Series, key) -> float | None:
    try:
        value = series.loc[key]
    except KeyError:
        return None
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    return None if pd.isna(value) else float(value)


def enrich(symbol: str, asset_class: str) -> dict:
    """Compute every stat derivable for one symbol from the local database."""
    t = _tables()
    try:
        row = t["catalog"].loc[(asset_class, symbol)]
    except KeyError:
        return {"stats": {}, "formulas": []}
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]

    class_total = int(t["class_total"][asset_class])
    labels = LEVEL_LABELS.get(asset_class, (None, None, None))
    stats: dict = {f"total {asset_class} in universe": class_total}
    formulas: list[str] = []

    # Peer counts at each category depth
    deepest_n, deepest_label = None, None
    for level, label in zip(("category_l1", "category_l2", "category_l3"), labels):
        value = row[level]
        if label is None or pd.isna(value):
            continue
        n = _get(t["level_sizes"][level], (asset_class, value))
        if n is None:
            continue
        stats[f"peers in same {label} ('{value}')"] = int(n)
        deepest_n, deepest_label, deepest_value = int(n), label, value

    if deepest_n is not None:
        share = deepest_n / class_total * 100
        stats[f"share of all {asset_class} in this {deepest_label} (%)"] = round(share, 2)
        formulas.append(
            f"share % = peers_in_{deepest_label.replace(' ', '_')} / total_{asset_class} * 100"
        )
        # Rarity: log-scaled inverse of niche size. 1-member niche = 100, whole class = 0.
        rarity = round(100 * (1 - math.log(deepest_n) / math.log(class_total)))
        stats["niche rarity score (0=everywhere, 100=one of a kind)"] = rarity
        formulas.append("rarity = 100 * (1 - ln(niche_size) / ln(class_total))")

    # Country context (equities)
    country = row["country"]
    if isinstance(country, str):
        n_country = _get(t["country"], (asset_class, country))
        if n_country:
            stats[f"listed equities from {country}"] = int(n_country)
        if deepest_n is not None:
            n_combo = _get(t["country_l3"], (asset_class, country, row["category_l3"]))
            if n_combo:
                stats[f"'{row['category_l3']}' companies in {country}"] = int(n_combo)
                formulas.append(
                    "it is one of exactly that many in its industry+country combo"
                )

    # Cross-listings: same name, same class, different tickers/exchanges
    listings = _get(t["name_listings"], (asset_class, row["name"]))
    if listings and listings > 1:
        stats["exchange listings worldwide (same name)"] = int(listings)

    # Survival rate in its industry group (equities carry a delisted flag)
    l2 = row["category_l2"]
    if isinstance(l2, str):
        delisted_rate = _get(t["delisted_l2"], (asset_class, l2))
        if delisted_rate is not None and delisted_rate > 0:
            stats[f"tickers in '{l2}' now delisted (%)"] = round(delisted_rate * 100, 1)
            formulas.append("delisted % = delisted_tickers_in_group / all_tickers_in_group * 100")

    # Market-cap bucket share among direct industry peers
    cap = row["market_cap"]
    if isinstance(cap, str) and deepest_n:
        n_same_cap = _get(t["cap_l3"], (asset_class, row["category_l3"], cap))
        if n_same_cap:
            stats[f"industry peers that are also {cap} (%)"] = round(
                n_same_cap / deepest_n * 100, 1
            )
            formulas.append("cap share % = same_cap_peers_in_industry / industry_peers * 100")

    # Exchange crowd
    exchange = row["exchange"]
    if isinstance(exchange, str):
        n_exchange = _get(t["exchange"], (asset_class, exchange))
        if n_exchange:
            stats[f"{asset_class} on the same exchange ({exchange})"] = int(n_exchange)

    # How much story material exists, relative to the class
    if bool(row["has_summary"]):
        arr = t["summary_sorted"][asset_class]
        if len(arr):
            pct = round(float(np.searchsorted(arr, row["summary_len"]) / len(arr)) * 100)
            stats["description length percentile within class"] = pct
            formulas.append("percentile = rank of summary length among all in class")

    return {"stats": stats, "formulas": formulas}


if __name__ == "__main__":
    import json
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    cls = sys.argv[2] if len(sys.argv) > 2 else "equities"
    print(json.dumps(enrich(sym, cls), indent=2))

"""Offline, feed-optimized access layer for the FinanceDatabase dataset.

Everything reads from local files under data/ - zero network calls.
Run scripts/build_dataset.py first (already done once) to (re)generate
the parquet + meta files from the raw clone.

Typical usage:

    from src import dataset as ds

    ds.catalog()                  # 353k-row unified candidate pool (cached)
    ds.load("equities")           # full equities table as DataFrame
    ds.options("etfs")            # {column: {value: count}} filter options
    ds.get("TSLA")                # one full record (incl. summary) as dict
    ds.sample(5, asset_class="equities", country="India")
    ds.load_fd("equities")        # a financedatabase.Equities instance,
                                  # offline, with .select()/.search()/.show_options()
"""

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PARQUET = DATA / "parquet"
META = DATA / "meta"
RAW_COMPRESSION = DATA / "raw" / "FinanceDatabase" / "compression"

ASSET_CLASSES = [
    "equities",
    "etfs",
    "funds",
    "indices",
    "currencies",
    "cryptos",
    "moneymarkets",
]

_FD_CLASS_NAMES = {
    "equities": "Equities",
    "etfs": "ETFs",
    "funds": "Funds",
    "indices": "Indices",
    "currencies": "Currencies",
    "cryptos": "Cryptos",
    "moneymarkets": "Moneymarkets",
}

_frames: dict[str, pd.DataFrame] = {}


def load(asset_class: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Load one asset class. Parquet first, raw bz2 as fallback.

    Full frames (columns=None) are cached in memory after first load.
    """
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"Unknown asset class {asset_class!r}. Use one of {ASSET_CLASSES}")
    if columns is None and asset_class in _frames:
        return _frames[asset_class]

    pq = PARQUET / f"{asset_class}.parquet"
    if pq.exists():
        df = pd.read_parquet(pq, columns=columns)
    else:
        df = pd.read_csv(
            RAW_COMPRESSION / f"{asset_class}.bz2", compression="bz2", index_col=0
        )
        if columns is not None:
            df = df[columns]

    if columns is None:
        _frames[asset_class] = df
    return df


@lru_cache(maxsize=1)
def catalog() -> pd.DataFrame:
    """The unified 353k-row candidate pool across all asset classes."""
    return pd.read_parquet(DATA / "catalog.parquet")


def options(asset_class: str) -> dict:
    """Filterable columns -> {value: count}, precomputed at build time."""
    with open(META / f"{asset_class}_options.json", encoding="utf-8") as f:
        return json.load(f)


def stats() -> dict:
    """Build statistics: row counts, columns, null rates, load timings."""
    with open(META / "stats.json", encoding="utf-8") as f:
        return json.load(f)


def load_fd(asset_class: str):
    """A financedatabase class instance backed by local parquet - no network.

    The upstream package always fetches from GitHub (its local mode only
    checks inside its own install dir), so we bypass __init__ and inject
    the locally loaded DataFrame. All .select()/.search()/.show_options()
    methods work as documented upstream.
    """
    import financedatabase as fd

    cls = getattr(fd, _FD_CLASS_NAMES[asset_class])
    instance = cls.__new__(cls)
    instance.data = load(asset_class)
    return instance


def get(symbol: str, asset_class: str | None = None) -> dict | None:
    """Full record for one symbol (including summary), searched across classes."""
    classes = [asset_class] if asset_class else ASSET_CLASSES
    for name in classes:
        df = load(name)
        if symbol in df.index:
            record = df.loc[symbol]
            if isinstance(record, pd.DataFrame):  # duplicate symbols: take first
                record = record.iloc[0]
            out = {"symbol": symbol, "asset_class": name}
            out.update({k: (None if pd.isna(v) else v) for k, v in record.items()})
            return out
    return None


def sample(
    n: int = 1,
    require_summary: bool = True,
    exclude_delisted: bool = True,
    seed: int | None = None,
    **filters,
) -> pd.DataFrame:
    """Random rows from the catalog - the seed of the feed's ordering engine.

    filters are equality matches on catalog columns, e.g.
    asset_class="equities", country="India", category_l1="Health Care".
    A list value matches any of its entries.
    """
    pool = catalog()
    if require_summary:
        pool = pool[pool["has_summary"]]
    if exclude_delisted:
        pool = pool[~pool["delisted"]]
    for column, value in filters.items():
        if isinstance(value, (list, tuple, set)):
            pool = pool[pool[column].isin(list(value))]
        else:
            pool = pool[pool[column] == value]
    if pool.empty:
        return pool
    return pool.sample(min(n, len(pool)), random_state=seed)

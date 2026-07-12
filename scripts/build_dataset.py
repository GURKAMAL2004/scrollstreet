"""Build the local, feed-optimized dataset from the raw FinanceDatabase clone.

Reads the 7 bz2-compressed CSVs from data/raw/FinanceDatabase/compression/,
then produces:

  data/parquet/<asset_class>.parquet   - full data, zstd parquet, fast columnar loads
  data/meta/<asset_class>_options.json - unique values + counts per categorical column
  data/meta/stats.json                 - row counts, columns, null rates, timings
  data/catalog.parquet                 - unified cross-asset candidate pool for the feed

Run:  .venv\\Scripts\\python.exe scripts\\build_dataset.py
Re-run any time after updating the raw clone (git pull in data/raw/FinanceDatabase).
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "FinanceDatabase" / "compression"
PARQUET = ROOT / "data" / "parquet"
META = ROOT / "data" / "meta"

ASSET_CLASSES = [
    "equities",
    "etfs",
    "funds",
    "indices",
    "currencies",
    "cryptos",
    "moneymarkets",
]

# Columns that are free text / identifiers - never useful as filter options
NON_CATEGORICAL = {
    "name",
    "summary",
    "website",
    "isin",
    "cusip",
    "figi",
    "composite_figi",
    "shareclass_figi",
    "zipcode",
    "city",
    "state",
}

# How each asset class maps onto the catalog's normalized category levels.
# Missing columns are handled gracefully (become None).
CATEGORY_MAP = {
    "equities": ("sector", "industry_group", "industry"),
    "etfs": ("category_group", "category", "family"),
    "funds": ("category_group", "category", "family"),
    "indices": ("category_group", "category", None),
    "currencies": ("base_currency", "quote_currency", None),
    "cryptos": ("cryptocurrency", None, None),
    "moneymarkets": (None, None, "family"),
}


def build_options(df: pd.DataFrame) -> dict:
    """Unique values + counts for every filterable categorical column."""
    options = {}
    for col in df.columns:
        if col in NON_CATEGORICAL or df[col].dtype != object:
            continue
        counts = df[col].dropna().value_counts()
        if len(counts) == 0 or len(counts) > 2000:
            continue
        options[col] = {str(k): int(v) for k, v in counts.items()}
    return options


def catalog_rows(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize one asset class into the unified catalog schema."""
    l1, l2, l3 = CATEGORY_MAP.get(name, (None, None, None))

    def col(c):
        return df[c] if c is not None and c in df.columns else None

    summary = df["summary"] if "summary" in df.columns else None
    out = pd.DataFrame(
        {
            "symbol": df.index.astype(str),
            "name": col("name"),
            "asset_class": name,
            "category_l1": col(l1),
            "category_l2": col(l2),
            "category_l3": col(l3),
            "country": col("country"),
            "currency": col("currency"),
            "exchange": col("exchange"),
            "market": col("market"),
            "market_cap": col("market_cap"),
            "delisted": (
                df["delisted"].fillna(False).astype(bool)
                if "delisted" in df.columns
                else False
            ),
            "has_summary": summary.notna() if summary is not None else False,
            "summary_len": (
                summary.str.len().fillna(0).astype(int) if summary is not None else 0
            ),
        }
    )
    return out


def main() -> None:
    PARQUET.mkdir(parents=True, exist_ok=True)
    META.mkdir(parents=True, exist_ok=True)

    stats = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(RAW),
        "asset_classes": {},
    }
    catalog_parts = []

    for name in ASSET_CLASSES:
        src = RAW / f"{name}.bz2"
        t0 = time.perf_counter()
        df = pd.read_csv(src, compression="bz2", index_col=0)
        t_csv = time.perf_counter() - t0

        pq_path = PARQUET / f"{name}.parquet"
        df.to_parquet(pq_path, engine="pyarrow", compression="zstd")

        t0 = time.perf_counter()
        pd.read_parquet(pq_path)
        t_pq = time.perf_counter() - t0

        options = build_options(df)
        with open(META / f"{name}_options.json", "w", encoding="utf-8") as f:
            json.dump(options, f, ensure_ascii=False, indent=1)

        catalog_parts.append(catalog_rows(name, df))

        stats["asset_classes"][name] = {
            "rows": int(len(df)),
            "columns": list(df.columns),
            "null_pct": {
                c: round(float(df[c].isna().mean()) * 100, 1) for c in df.columns
            },
            "bz2_load_seconds": round(t_csv, 2),
            "parquet_load_seconds": round(t_pq, 2),
            "bz2_mb": round(src.stat().st_size / 1e6, 2),
            "parquet_mb": round(pq_path.stat().st_size / 1e6, 2),
        }
        print(
            f"{name:>14}: {len(df):>7,} rows | "
            f"bz2 load {t_csv:5.1f}s -> parquet load {t_pq:5.2f}s"
        )

    catalog = pd.concat(catalog_parts, ignore_index=True)
    catalog.to_parquet(ROOT / "data" / "catalog.parquet", engine="pyarrow", compression="zstd")
    stats["catalog"] = {
        "rows": int(len(catalog)),
        "columns": list(catalog.columns),
        "mb": round((ROOT / "data" / "catalog.parquet").stat().st_size / 1e6, 2),
    }

    with open(META / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print(f"\ncatalog: {len(catalog):,} rows -> data/catalog.parquet")
    print("stats  -> data/meta/stats.json")


if __name__ == "__main__":
    main()

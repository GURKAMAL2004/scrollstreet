# Data guide — what every file is, schemas, and how to load things

## Folder map

```
D:\finance llm desc\
├── .venv\                      Python 3.14 virtualenv (financedatabase, pyarrow, pandas 3.x)
├── main.py                     end-to-end smoke test — run this to verify everything works
├── requirements.txt            pinned top-level deps
├── scripts\
│   └── build_dataset.py        raw bz2 → parquet + meta + catalog (idempotent, re-run after git pull)
├── src\
│   └── dataset.py              THE access layer — import this from all future code
├── data\
│   ├── raw\FinanceDatabase\    shallow git clone = source of truth (git pull to refresh)
│   │   ├── compression\        the 7 bz2 files the build reads  (~19 MB)
│   │   ├── database\           per-country editable CSVs (upstream's contributor format)
│   │   └── financedatabase\    upstream package source (useful to read, not imported)
│   ├── parquet\                fast columnar copies of all 7 asset classes (~27 MB)
│   ├── meta\                   *_options.json per class + stats.json
│   └── catalog.parquet         unified candidate pool, 353,822 rows, 4.75 MB
└── readme_claude\              this documentation
```

## The numbers (measured on this machine, 2026-07-12)

| Asset class | Rows | Parquet size | bz2 load (cold) | Parquet load |
|---|---:|---:|---:|---:|
| equities | 161,015 | 20.1 MB | 6.4s | 0.13s |
| indices | 91,181 | 1.8 MB | 0.5s | 0.02s |
| funds | 57,853 | 3.1 MB | 1.0s | 0.03s |
| etfs | 36,483 | 1.8 MB | 0.7s | 0.02s |
| cryptos | 3,367 | 0.07 MB | ~0s | 0.01s |
| currencies | 2,556 | 0.09 MB | ~0s | 0.01s |
| moneymarkets | 1,367 | 0.04 MB | ~0s | 0.01s |
| **catalog (unified)** | **353,822** | **4.75 MB** | — | **0.08s** |

Feed-usable pool (has a text summary AND not delisted): **216,429 symbols**
(equities 90,622 · funds 48,119 · indices 42,790 · etfs 28,789 · cryptos 3,361 ·
currencies 2,057 · moneymarkets 691). 117 countries, 559 distinct top-level
categories across classes. 9,846 equities are flagged delisted.

## Schemas

### Per-class Parquet (`data/parquet/<class>.parquet`) — index is `symbol`

- **equities**: name, summary, currency, sector, industry_group, industry, exchange,
  mic, market, country, state, city, zipcode, website, market_cap, isin, cusip, figi,
  composite_figi, shareclass_figi, delisted
- **etfs**: name, currency, summary, category_group, category, family, exchange, mic, isin
- **funds**: name, currency, summary, category_group, category, family, exchange, mic
- **indices**: name, currency, summary, category_group, category, exchange, mic
- **currencies**: name, base_currency, quote_currency, summary, exchange
- **cryptos**: name, cryptocurrency, currency, summary, exchange
- **moneymarkets**: name, currency, summary, family, exchange

### Unified catalog (`data/catalog.parquet`)

| Column | Meaning |
|---|---|
| symbol | ticker (unique within class, not globally — e.g. cross-listings) |
| name | display name |
| asset_class | one of the 7 class names |
| category_l1..l3 | normalized category ladder, broadest first (see mapping below) |
| country | equities only; NaN elsewhere |
| currency / exchange / market | trading venue info |
| market_cap | equities only: Nano Cap … Mega Cap |
| delisted | True for 9,846 dead equities (kept deliberately — see decision D8) |
| has_summary / summary_len | whether/how much story material exists for the LLM |

Category-level mapping per class:
equities → sector / industry_group / industry ·
etfs, funds → category_group / category / family ·
indices → category_group / category / — ·
currencies → base_currency / quote_currency / — ·
cryptos → cryptocurrency / — / — ·
moneymarkets → — / — / family

### Meta files (`data/meta/`)

- `<class>_options.json` — `{column: {value: count}}` for every categorical column.
  Loads instantly; use for filter UIs, taste vocabularies, prompt constraints.
- `stats.json` — build timestamp, per-class rows/columns/null-percentages/timings.
  Check `null_pct` before relying on any column (e.g. many equities lack `isin`).

## How to load things (recipes)

Always use the venv: `.venv\Scripts\python.exe`, and import from project root.

```python
from src import dataset as ds

ds.catalog()                          # 353k-row pool, cached, 0.08s first time
ds.load("equities")                   # full class DataFrame (cached)
ds.load("equities", columns=["name", "sector", "country"])   # columnar partial read
ds.options("etfs")["category_group"]  # {'Fixed Income': ..., 'Equities': ..., ...}
ds.stats()                            # build metadata
ds.get("TSLA")                        # full record incl. summary, as a dict
ds.sample(5, asset_class="equities", country="India", category_l1="Health Care")
ds.sample(1, exclude_delisted=False)  # allow dead-company stories

# Full upstream API (select/search/show_options), offline:
equities = ds.load_fd("equities")
equities.select(country="Netherlands", industry="Insurance", market="Euronext Amsterdam")
equities.search(summary="Robotics", index=".F")
```

## Maintenance

Refresh universe (upstream updates weekly, Sundays for US exchanges):

```powershell
git -C "D:\finance llm desc\data\raw\FinanceDatabase" pull
& "D:\finance llm desc\.venv\Scripts\python.exe" "D:\finance llm desc\scripts\build_dataset.py"
```

The build is idempotent — it fully regenerates `data/parquet/`, `data/meta/`, and
`data/catalog.parquet` in ~15s. Verify anytime with:

```powershell
& "D:\finance llm desc\.venv\Scripts\python.exe" "D:\finance llm desc\main.py"
```

## Gotchas discovered while building (so you don't rediscover them)

1. `financedatabase`'s `use_local_location=True` does NOT respect `base_url` — it
   looks inside its own install directory. Use `ds.load_fd()` instead (decision D4).
2. The bz2 files live in the repo's `compression/` folder, not `database/`.
   `database/` holds per-country contributor CSVs (85 files for equities alone).
3. Symbols repeat across exchanges (cross-listings). `only_primary_listing=True` in
   upstream `.select()`, or dedupe on `name`, when you want one row per company.
4. `pandas 3.x` is installed — string columns are Arrow-backed; some old StackOverflow
   idioms (chained assignment, `.append`) don't apply.
5. Some catalog fields are NaN by design (country only exists for equities). Filters
   in `ds.sample()` treat NaN as non-matching — filtering `country="India"` silently
   restricts you to equities. Intentional, but remember it.

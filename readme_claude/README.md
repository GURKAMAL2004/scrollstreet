# readme_claude — Claude's working notes for this project

This folder is the memory of how this project was set up: what was built, why every
decision was made, and how it is meant to guide the next phase (the local,
attention-driven finance feed). Written 2026-07-12 by Claude while doing the work.

## What this project is right now

`D:\finance llm desc` contains the **FinanceDatabase** (353,822 financial symbols:
equities, ETFs, funds, indices, currencies, cryptos, money markets) fully downloaded,
installed, converted into a fast local format, and wrapped in a small Python API —
**all usable 100% offline**. This is the content universe for the future feed app.
Nothing of the feed itself is built yet, on purpose: this phase was only about
arranging the data so the feed can later be built with extreme efficiency and
minimal resources.

## Read these in order

| File | What it answers |
|---|---|
| [01_DECISIONS.md](01_DECISIONS.md) | Why each choice was made (and what was rejected) |
| [02_DATA_GUIDE.md](02_DATA_GUIDE.md) | What every file/folder is, schemas, how to load things, real performance numbers |
| [03_ROADMAP_TO_FEED.md](03_ROADMAP_TO_FEED.md) | How this arrangement maps onto the feed vision, and the concrete next steps |
| [04_THOUGHT_LOG.md](04_THOUGHT_LOG.md) | Chronological narrative of the session — every thought, surprise, and course-correction |
| [05_FEED_V1.md](05_FEED_V1.md) | The working feed: random-walk scroll, 25-post cap, LLM cards with locally computed math |
| [06_THE_MAP.md](06_THE_MAP.md) | The embedding manifold: 226k instruments arranged by meaning, density, neighbourhoods, and the /map page |

## The 30-second summary

1. **Raw source of truth**: shallow git clone of JerBouma/FinanceDatabase at
   `data/raw/FinanceDatabase/` (171 MB). Refresh anytime with `git pull` — upstream
   updates weekly.
2. **Fast derived layer**: `scripts/build_dataset.py` converts the raw bz2 CSVs into
   Parquet (`data/parquet/`), precomputed filter options (`data/meta/*_options.json`),
   and one unified 4.75 MB `data/catalog.parquet` — the feed's candidate pool.
3. **Access API**: `src/dataset.py` — `catalog()`, `load()`, `options()`, `get()`,
   `sample()`, `load_fd()`. Zero network. Catalog loads in 0.08s; equities (161k rows
   with full text summaries) in ~0.15s.
4. **Feed-usable pool**: 216,429 symbols have a text summary and are not delisted —
   each one is a potential story card for the LLM.

## The one rule that shaped everything

> The feed will make thousands of tiny queries per session (pick next card, filter by
> category, sample by mood-vector). Every query must feel instant on a normal laptop
> with an LLM already eating the RAM/VRAM. So: pay the parsing cost **once at build
> time**, never at feed time.

Everything else follows from that rule.

# Feed v1 — random-walk scroll, 25 posts, LLM cards with computed math

Built 2026-07-12, immediately after the data substrate. This implements roadmap
Steps 1–4 in their minimal form: card generator, event log, ordering engine
(random walk only), and a scrollable UI.

## How to run

```powershell
cd "D:\finance llm desc"
.venv\Scripts\python.exe server.py
# open http://127.0.0.1:8765 in a browser and scroll
```

Requires Ollama running (it autostarts on this machine). Without it, the feed
still works — cards fall back to a data-only template (`"model": "fallback"`).

Better prose, slower cards: `$env:FEED_MODEL = "qwen2.5:14b-instruct-q4_K_M"`
before starting the server. Default is the 1.5b (≈3–4s/card measured here).

## The pipeline per scroll

```
scroll → web/index.html asks /api/next
       → src/ordering.py  random_walk: uniform draw from 216k usable symbols, minus already-seen
       → src/dataset.py   get(symbol): full record incl. summary (ms, local parquet)
       → src/enrich.py    ~13 computed stats + the formulas that made them
       → src/cards.py     prompt → Ollama → JSON card (headline/hook/story/wild_fact/open_question)
       → UI renders card; dwell + events appended to data/feed/events.jsonl
```

## The "extra mathematics" (what enrich.py computes and why)

FinanceDatabase has no prices — so every number is a **census statistic** computed
locally over the 353k-symbol catalog: peer counts at 3 category depths, share-of-
universe %, a log-scaled niche rarity score `100·(1−ln(niche)/ln(class_total))`,
country totals and industry+country combo counts ("one of exactly 154 US automobile
companies"), cross-listing counts, delisted % per industry group (survival rate),
market-cap-bucket share among direct peers, same-exchange crowd size, and a
description-length percentile. The formulas are passed to the LLM alongside the
values so it can explain them instead of guessing.

**The anti-hallucination contract:** the system prompt forbids any number not
present in the DATA/MATH sections, and explicitly states the MATH numbers are
counts/percentages, never dollars. This was added after observing the 1.5b model
present "57,853 total funds" as "$57,853 in assets under management".

## Decisions specific to v1

- **stdlib `http.server`, no FastAPI** — two endpoints and one HTML file don't
  justify a framework; zero new dependencies keeps the "least resources" promise.
  Bound to 127.0.0.1 (no firewall prompt, nothing exposed).
- **25-card cap enforced in BOTH places** — server refuses card 26 per session
  (source of truth), client renders a recap card with session stats. Client-only
  caps are decoration; server-only caps make the UI end ungracefully.
- **Lookahead buffer client-side (3 cards)** — the UI prefetches sequentially up to
  viewing+3, so the ~3.5s generation hides behind reading time. Sequential, not
  parallel, because one Ollama instance serializes generations anyway.
- **`keep_alive: 30m`** on Ollama calls — keeps the model warm across the whole
  session instead of paying model-load per card.
- **Fast 1.5b model by default** — feed cadence beats prose quality for v1; the
  model is a config knob, not a rewrite. Known limitation: the 1.5b occasionally
  misattributes a stat even with the hard rules (it once described the rarity score
  as a "rank"). The 14b is markedly more faithful.
- **Events logged from card #1** (`data/feed/events.jsonl`): `card_generated`
  (with strategy + model + gen_ms), `dwell` (per card, ms, via IntersectionObserver
  at 60% visibility), `session_end`. This is the training data for roadmap Step 5 —
  the file format was chosen before the first real scroll, as planned.
- **Random walk excludes delisted + summary-less symbols** via the same flags built
  into the catalog in the substrate phase — no new filtering logic was needed,
  which is the substrate paying off.

## What v1 deliberately does not do

No personalization, no rabbit-hole/whiplash/serpentine (Step 3's full version),
no persona vectors, no bandit — the event log now accumulates the data those will
need. Next concrete move: implement dwell-weighted category scores from
events.jsonl and blend a second strategy into `ordering.next_symbols()`.

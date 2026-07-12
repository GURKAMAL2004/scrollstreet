# Roadmap — how this arrangement becomes the feed

> **Update 2026-07-12 (same day, later session):** Steps 1, 2, and 4 shipped, and
> Step 3 shipped in its random-walk-only form — see [05_FEED_V1.md](05_FEED_V1.md).
> Next up: the remaining Step 3 strategies, then Step 5 (taste model over the now
> accumulating events.jsonl).

The vision (from the project brief): an infinite vertical feed of LLM-generated
financial story cards, fully local (Ollama, zero external APIs), driven by an
ordering engine that learns attention patterns — random walk, rabbit-hole,
contrastive whiplash, curiosity chaining, diversity serpentine, variable reward —
on top of a personalization engine with persona vectors and a local bandit.

We did NOT build any of that yet. We built the ground it stands on. Here is the
explicit mapping from what exists to what comes next, and why the next steps are
ordered the way they are.

## How today's arrangement serves each future component

| Future component | What it needs | What already provides it |
|---|---|---|
| Card content | rich metadata + a text seed per symbol | 216,429 symbols with summaries; `ds.get(symbol)` returns everything in ms |
| Ordering engine | one flat, cheap, always-in-RAM candidate pool with comparable axes | `catalog.parquet`: 4.75 MB, 0.08s load, normalized `category_l1..l3` across all 7 classes |
| Rabbit-hole mode | "give me more like this, one level deeper" | category ladder: same l1 → same l2 → same l3 = increasing depth; counts in `*_options.json` say when a hole is exhausted |
| Contrastive whiplash | "maximum distance from previous card" | distance is computable on catalog columns (different asset_class > different l1 > different country…) with zero extra data |
| Diversity serpentine | "never two same-category in a row" | same columns; a 5-line constraint on the sampler |
| Variable reward / bandit | discrete arms with known sizes | arms = category values from `*_options.json`, with candidate counts as priors |
| Persona / taste vectors | a stable vocabulary to express taste over | the exact value sets in `*_options.json` (and later, summary embeddings) |
| Zero-network guarantee | everything on disk | raw clone + parquet + injected `load_fd`; nothing imports `requests` at feed time |

## The build order (and the logic of the order)

Each step is small, testable alone, and none requires redoing an earlier one —
that's the criterion I used to order them.

### Step 1 — Card generator (`src/cards.py`)
One function: `record → prompt → Ollama → card JSON` (headline, hook, surprising
angle, open question). Test it standalone in the terminal: `python -m src.cards TSLA`.
*Why first:* it's the only piece with creative risk (prompt quality decides whether
cards feel like stories or like Wikipedia). It needs zero infrastructure beyond
`ds.get()` — already done — and an `ollama` pip install. Validate the magic before
building plumbing around it.
*Efficiency note:* generate with a small fast model first (e.g. an 8B), keep the
prompt under ~1k tokens by sending only the fields the card type needs, truncate
summaries to ~500 chars — they front-load the interesting part.

### Step 2 — Event log (`data/feed/events.jsonl`)
Append-only JSONL: `{ts, symbol, card_id, dwell_ms, action, session_id, strategy}`.
*Why second:* every learning component downstream is a consumer of this file. Logging
must exist before the first real scroll ever happens, or that data is lost forever.
JSONL because it's append-cheap, crash-safe, and trivially replayable into any future
store — no schema commitment today.

### Step 3 — Ordering engine v1 (`src/ordering.py`), rule-based, no ML
Implement the strategies as pure functions over (catalog, recent_history):
`random_walk`, `rabbit_hole`, `whiplash`, `serpentine`. Blend = weighted choice among
strategies per swipe.
*Why rule-based first:* every strategy in the brief is expressible as filters +
sampling over the catalog — measurable in milliseconds, debuggable, and it produces
the interaction data the ML version will need. Bandits/RL with no logged data would
be learning from noise.

### Step 4 — Minimal feed UI
Simplest thing that scrolls: a local FastAPI + one HTML page (or even a terminal
pager to start). One endpoint: `GET /next` → ordering engine picks symbol → cards.py
renders → log event on swipe.
*Why now:* closes the loop; from here on, every session generates training data.
*Efficiency note:* pre-generate 2–3 cards ahead in a background thread so scroll
never waits on the LLM; at ~1–3s per card on a local model, a lookahead buffer is
the difference between "feels like TikTok" and "feels like loading screens".

### Step 5 — Taste model + bandit (replace hand-tuned weights)
Start with counts: per category value, a decayed dwell-time score from events.jsonl.
Then a Thompson-sampling bandit over strategy choice and over category arms.
Persona vectors = clustering event history by time-of-day/session; switch priors per
cluster. All local, all small (scikit-learn is already installed as a financetoolkit
dependency).

### Step 6 — Embeddings layer (only when needed)
Embed all summaries once at build time (local model, e.g. via Ollama embeddings) →
`data/embeddings.parquet` keyed by symbol. Enables "semantic rabbit holes" and
curiosity chaining beyond the category ladder.
*Why last:* it's the most compute-hungry build step (~216k texts) and the category
ladder already approximates similarity well enough to validate everything else.
This is also the point where DuckDB/sqlite-vec earns its way in (see decision D6).

## Resource budget logic (the "least resources" constraint, quantified)

Steady-state feed session on a normal laptop:
- catalog resident in RAM: ~40 MB as DataFrame — negligible
- per-card data fetch: one indexed parquet lookup, <10 ms, cached per class
- per-card LLM call: the actual budget — 1–3s on a small local model; hidden by the
  Step-4 lookahead buffer
- event logging: appends to JSONL — negligible
- everything else (options, stats): loaded once, KBs

The design goal was that **the LLM is the only expensive thing in the loop**, so all
optimization effort concentrates on one lever (model choice, quantization, prompt
size, lookahead depth) instead of being smeared across the stack.

## What I deliberately did NOT do (scope discipline)

- No feed code, no UI, no Ollama integration — the brief said "we are not directly
  building this", so the deliverable is the substrate plus this map.
- No embeddings yet (Step 6 explains when).
- No FinancialModelingPrep / FinanceToolkit wiring — it's installed (came with the
  package) and `to_toolkit()` works, but it needs an API key and live quotes are
  explicitly out of the local-first vision. It stays available for the day you want
  historical-data cards.
- No deletion or "cleaning" of raw data — every filter (delisted, no-summary) is a
  runtime flag, never a build-time drop. Editorial decisions stay reversible.

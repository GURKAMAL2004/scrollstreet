# Thought log — the session as it actually happened

A chronological, honest record of what I did, what I expected, what surprised me,
and where I changed course. Written 2026-07-12.

## 1. Parsing the request

The ask had three layers: (a) install FinanceDatabase and make it usable from
`D:\finance llm desc`; (b) arrange the dataset so a future attention-driven feed can
be built "with extreme efficiency and using the least resources"; (c) document every
thought and decision in a `readme_claude` folder. Crucially: *not* build the feed
itself. So I treated this as a data-engineering phase with the feed brief as the
requirements document. Before touching anything I re-read the feed brief and
extracted what the data layer must be good at: thousands of tiny "pick next card"
queries, instant metadata filters, per-symbol story material for the LLM, strict
offline operation, small RAM footprint (the LLM owns the machine's resources).
That produced the one rule everything follows: pay parsing costs at build time,
never at feed time.

## 2. Getting the pieces (and the first fork in the road)

Started the shallow git clone in the background (it was the long pole) while
creating the venv in parallel. First fork: clone the whole repo (171 MB) vs download
just the 7 bz2 data files (19 MB)? Chose the clone — weekly upstream updates become
`git pull`, and the repo carries useful extras (package source, categorization
files). Documented as decision D1. Python turned out to be 3.14.6; I half-expected
wheel availability problems for pandas/pyarrow, but everything installed cleanly —
`financedatabase 2.4.0` pulled in `pandas 3.0.3`, `financetoolkit`, `yfinance`,
`scikit-learn`. Noted for later: scikit-learn being already present is convenient
for the future bandit/persona work.

## 3. The surprise: "local mode" isn't local the way you'd hope

I assumed `fd.Equities(base_url=..., use_local_location=True)` would just point at
our clone. Before relying on it I read the actual source in the clone
(`financedatabase/helpers.py`) — and found `use_local_location=True` *ignores*
`base_url` entirely: it hardcodes a path relative to the installed package
(`Path(__file__).parent.parent / "compression"`), which for a pip install points
into site-packages where no data exists. The flag only works if you run from a repo
checkout. This flipped my plan: instead of configuring the package, bypass its
constructor entirely — `cls.__new__(cls)` + inject our DataFrame into `.data`. I
checked that `.data` is the only instance state the class methods use (true in
v2.4.0), accepted that as a version-pinned risk, and isolated it in one 10-line
function. Second, smaller surprise in the same read: the runtime data lives in the
repo's `compression/` folder, not `database/` — `database/` is the human-editable
per-country CSV layer (85 files for equities alone). Both went into the gotchas list.

## 4. Choosing the storage arrangement

Measured before optimizing: cold bz2 load of equities was 6.4s. That's fine for a
script, fatal for an app that should feel like TikTok. Parquet with zstd brought it
to 0.13s warm — and columnar reads mean the ordering engine can pull three metadata
columns without parsing 161k text summaries. Considered SQLite/DuckDB and decided
*against* — measured load times showed no problem an engine would solve today, and
"least resources" cuts against carrying an unused dependency. Wrote down the
explicit trigger for revisiting (full-text search / embeddings) so the decision
stays honest rather than becoming dogma.

The catalog idea came directly from the feed brief's ordering strategies: every one
of them ("maximum distance from previous card", "never two same-category in a row",
"go deeper into this niche") is a computation over *comparable category axes across
asset classes*. But the classes have different schemas. So: normalize into
`category_l1/l2/l3`, keep it light by excluding summaries (which are ~80% of the
bytes), and the entire 353k-symbol universe becomes a 4.75 MB always-in-RAM table.
The two-tier split (light catalog decides, heavy per-class files render) is the
load-bearing design decision of the whole session.

## 5. Iterating on the build

First build ran clean: 353,822 rows total. Then I inspected the actual column lists
in the generated stats.json and caught two things my first catalog mapping missed:
(a) equities carry a `delisted` flag — 9,846 dead companies. Kept them, flagged,
excluded by default in `sample()` — for a storytelling feed, dead companies are
premium content, and dropping data at build time is irreversible. (b) currencies
(base/quote), cryptos (cryptocurrency), and moneymarkets (family) had category-like
columns I'd mapped to nothing. Fixed the mapping, rebuilt (~15s — cheap rebuilds
were themselves a design goal), and confirmed all seven classes carry a `summary`
column, which means every asset class can produce story cards, not just equities.

## 6. Verification, not vibes

`main.py` became a smoke test with three assertions of usability: the catalog loads
fast (0.08s measured), the upstream API works offline (I reproduced the upstream
README's own example — Dutch insurers on Euronext Amsterdam — and got the identical
3 companies: AGN.AS, ASRNL.AS, NN.AS), and a random card-ready record comes out
whole (it drew an Indian nano-cap IT company with a full summary — exactly the
"obscure but story-worthy" material the feed wants). Also computed the number that
matters most for the product: 216,429 symbols are feed-usable (summary present, not
delisted). That's the real content universe size.

## 7. What I'd tell the next session

Start at `readme_claude/03_ROADMAP_TO_FEED.md`, Step 1 (card generator): the data
substrate is done and verified; the next creative risk is prompt design, and
`ds.get(symbol)` already hands you everything a prompt needs. Don't re-derive the
storage decisions — they're argued in 01_DECISIONS.md with their revisit-triggers
written down. And run `main.py` first; if it passes, everything below it is sound.

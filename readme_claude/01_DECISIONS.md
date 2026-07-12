# Decision log — every choice, its logic, and what it enables next

Each entry: what I decided, what I rejected, why, and how it guides the next phase.

---

## D1. Keep a full git clone as the raw source of truth (not just downloaded data files)

**Decision:** `git clone --depth 1` of JerBouma/FinanceDatabase into
`data/raw/FinanceDatabase/` (171 MB).

**Rejected alternative:** downloading only the 7 bz2 data files (~19 MB) from
raw.githubusercontent.com.

**Why:** The upstream database is updated weekly (every Sunday for US exchanges).
With a clone, refreshing the entire universe is one command — `git pull` — followed by
re-running the build script. With loose file downloads, updating means maintaining a
list of URLs and hoping upstream never renames things. The clone also gives us, for
free: the package source code (which I ended up needing to read — see D4), the
per-country editable CSVs in `database/`, the categorization files in
`compression/categories/`, and example notebooks. `--depth 1` keeps history out, so
we pay 171 MB instead of multiple GB.

**How it guides us next:** the refresh loop for the feed is already solved:
`git pull` → `python scripts/build_dataset.py` → feed sees fresh universe. This could
later become a scheduled task; nothing needs redesign.

---

## D2. Convert everything to Parquet at build time; never read bz2 at runtime

**Decision:** one-time conversion of all 7 asset classes to zstd-compressed Parquet in
`data/parquet/`, via `scripts/build_dataset.py`.

**Rejected alternatives:** (a) reading the bz2 CSVs directly every run — measured at
3–6.4s for equities alone, and it re-pays that cost on every process start;
(b) a SQLite/DuckDB database — see D6.

**Why:** measured on this machine: equities went from **6.4s (bz2 CSV, cold) to
0.13s (Parquet, warm)** — roughly a 20–50x load speedup. Parquet is columnar, so the
feed can read *only* the columns it needs (e.g. just `sector` + `country` for the
ordering engine, without parsing 161k text summaries). Storage cost is modest: 27 MB
of Parquet vs 19 MB of bz2. This is the direct implementation of the project's core
rule: pay parsing once at build time.

**How it guides us next:** the feed's process start (open app → first card) will be
bounded by Ollama model load, not by data. Data will never be the latency bottleneck.

---

## D3. Build one unified `catalog.parquet` across all asset classes

**Decision:** a single 353,822-row, 4.75 MB table with normalized columns:
`symbol, name, asset_class, category_l1, category_l2, category_l3, country, currency,
exchange, market, market_cap, delisted, has_summary, summary_len`.

**Rejected alternative:** letting the future feed juggle 7 differently-shaped tables.

**Why:** the feed's ordering engine (random walk, rabbit-hole, contrastive whiplash,
diversity serpentine…) needs to treat the whole universe as **one candidate pool**
with comparable axes. Equities have sector/industry_group/industry; ETFs and funds
have category_group/category/family; cryptos have cryptocurrency; currencies have
base/quote. I mapped them all onto three generic levels (l1 = broadest) so
"category distance between two cards" is computable with one rule regardless of asset
class. The heavy text (summaries) is deliberately NOT in the catalog — only
`has_summary`/`summary_len` flags — which is why 353k rows fit in 4.75 MB and load in
0.08s. The full record is fetched per-symbol only when a card is actually being
generated (2-tier design: light index for deciding, heavy store for rendering).

**How it guides us next:** the ordering engine's entire state can be
"catalog in RAM (~40 MB as DataFrame) + a cursor". Selection strategies become
one-liner filters/samples. This mirrors the SilverTorch 'index as model' spirit the
project brief cites: one flat candidate pool the scoring logic can attend over,
instead of query-per-source federation.

---

## D4. Bypass the pip package's networking; inject local data into its classes

**Decision:** `src/dataset.py::load_fd()` creates `financedatabase.Equities` etc. via
`cls.__new__(cls)` and injects our locally-loaded DataFrame into `.data`, skipping
`__init__` entirely.

**Rejected alternatives:** (a) using the package as-is — it downloads from GitHub on
every class instantiation, violating the offline requirement; (b) its documented
`use_local_location=True` flag — **I read the source
(`financedatabase/helpers.py`) and found this flag ignores `base_url` and looks for a
`compression/` folder relative to the installed package inside site-packages**, which
doesn't exist for a pip install. It only works for people running from a repo
checkout; (c) monkeypatching `financedatabase.helpers.file_path` — works, but still
parses bz2 (slow path) and depends on a module-global staying stable across versions.

**Why:** the upstream `.select()` / `.search()` / `.show_options()` methods are
genuinely good (list filters, case-insensitive search, primary-listing filter,
delisted exclusion) and are documented in the upstream README — no reason to rewrite
them. Injecting `.data` keeps 100% of that API while making it offline and ~20x
faster to initialize. Verified: the README's "Dutch insurance on Euronext Amsterdam"
example returns the identical 3 companies (AGN.AS, ASRNL.AS, NN.AS) fully offline.

**Risk accepted:** `__new__`-injection depends on upstream classes keeping `.data` as
their only instance state. Checked for v2.4.0: true. If a future version breaks it,
`load_fd` is 10 lines and isolated in one place.

**How it guides us next:** exploratory/query work (finding niches, building taste
clusters) uses the mature upstream API; the hot feed path uses our own thin
`catalog()`/`sample()`/`get()` primitives. Two tools, one data layer.

---

## D5. Precompute filter options and stats as JSON at build time

**Decision:** `data/meta/<class>_options.json` (every categorical column → value →
count) and `data/meta/stats.json` (rows, columns, null rates, timings, sizes).

**Why:** the upstream `fd.show_options()` fetches categorization files from GitHub —
network again. Also, counts matter to a feed: the ordering engine should know that
"Health Care equities in India" has hundreds of candidates while "Insurance in
Iceland" may have 2, *before* querying — that's how you avoid dead-end rabbit holes.
A JSON file is instant to load, human-readable, and diffable after each rebuild.

**How it guides us next:** these files are the vocabulary of the taste model. User
taste vectors will be over these exact category values; the LLM prompt template can
list valid categories from here; the bandit's arms can be defined over them.

---

## D6. No database engine (SQLite/DuckDB) — yet

**Decision:** plain Parquet + pandas, nothing else.

**Why:** with a 4.75 MB catalog that loads in 0.08s and full-class Parquet loads
under 0.2s, an engine adds a dependency, a file format, and a query language while
solving no measured problem. "Least resources" means the RAM story matters more: the
feed will hold only the catalog (~40 MB) resident and lazy-load summaries per card.
**Revisit trigger (written down so we notice):** if/when we add full-text search over
summaries or vector similarity over embeddings, bring in DuckDB (it queries our
existing Parquet in place — no migration) or sqlite-vec for embeddings. The current
layout was chosen so that upgrade is additive, not a rewrite.

---

## D7. Project-local virtualenv (`.venv/`) on Python 3.14

**Decision:** dedicated venv inside the project; `financedatabase 2.4.0`, `pyarrow`,
and (as a dependency of financedatabase) `financetoolkit`, `yfinance`, `pandas 3.x`
installed into it.

**Why:** the feed will accumulate dependencies (ollama client, maybe fastapi,
embeddings). Isolating them keeps the system Python clean and makes the project
portable/deletable. Note: `pandas 3.0.3` came in as a dependency — newer than most
tutorials assume; if upstream code someday misbehaves, suspect pandas 3.x behavior
changes first (copy-on-write is default, etc.).

**How to use it:** always run through `.venv\Scripts\python.exe` (or activate with
`.venv\Scripts\Activate.ps1`).

---

## D8. Include `delisted` in the catalog instead of dropping delisted rows

**Decision:** keep all 9,846 delisted equities in Parquet and catalog, flagged;
`sample()` excludes them by default but can include them.

**Why:** upstream intentionally retains delisted tickers for historical research —
and for a *storytelling* feed, dead companies are premium content ("this company no
longer exists — here's how it died"). Dropping data at build time is an irreversible
editorial decision; flagging it keeps every future option open at the cost of one
boolean column.

---

## D9. `summary` kept only in per-class Parquet, not in the catalog

**Decision:** two-tier storage — light catalog for choosing, heavy per-class files
for rendering.

**Why:** summaries are ~80% of the data's bytes. Putting them in the catalog would
make the always-resident candidate pool ~10x heavier for no decision-making benefit —
the ordering engine picks *what* to show from metadata; the LLM needs the summary
only for the *one* card being generated. `ds.get(symbol)` fetches it in
milliseconds from the class Parquet (cached after first touch).

**How it guides us next:** this is exactly the retrieval→generation split the feed
needs, and it's also where embeddings will plug in later: embed summaries once at
build time, store vectors keyed by symbol, keep the catalog light.

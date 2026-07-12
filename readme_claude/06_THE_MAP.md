# The Map — an embedding manifold over the whole universe

Built 2026-07-12, immediately after feed v1. This is the honest implementation of
the only salvageable idea in the "Financial Occupancy Network" proposal (see the
conversation record in 04/05): a latent space you can navigate — but over *content
similarity* where we have real data, not over market dynamics where we don't.

## What it is

Every one of the ~226,000 symbols with a description (delisted included — dead
companies are part of the landscape) gets a 768-dimensional vector from a local
embedding model (`nomic-embed-text` via Ollama). Instruments whose descriptions
*mean* similar things get nearby vectors. On top of that, three derived layers:

| Layer | File | What it gives the feed |
|---|---|---|
| Vectors | `data/embeddings.parquet` (~340 MB, float16) | true nearest-neighbour queries ("more like this") |
| Map metadata | `data/map_meta.parquet` | 2D coords (PCA) for viewing, k-NN **density** (crowding), 400 k-means **neighbourhoods** |
| Neighbourhood labels | `data/meta/map_clusters.json` | human names like "equities · Banks · Japan" |

## How to (re)build

```powershell
.venv\Scripts\python.exe scripts\build_map.py          # embed + finish (~1.5h first time)
.venv\Scripts\python.exe scripts\build_map.py finish   # recompute map layers only (~2 min)
```

Embedding is **chunked and resumable**: progress lands in
`data/embeddings/chunks/` (2048 symbols per file); re-running skips finished
chunks. Interrupting costs at most one chunk. Rebuild is only needed when the
underlying database is refreshed (git pull + build_dataset.py), and then only
`embed` re-runs fully if you delete the chunks — otherwise stale-but-fine.

## How to use it

```python
from src import manifold as mf
mf.where("TSLA")            # coordinates, neighbourhood name, loneliness percentile
mf.neighbors("TSLA", k=10)  # nearest instruments by meaning (768-dim cosine)
mf.lonely(10)               # the most remote outposts of the whole map
mf.explore(seen, n=5)       # sparse-biased sampler - curiosity v0 for the feed
```

Visual map: **http://127.0.0.1:8765/map** — all points on one canvas, colored by
asset class (palette validated with the dataviz method against the dark surface;
worst adjacent CVD ΔE 23.6), pan/zoom, hover tooltips, click legend chips to
toggle classes, largest neighbourhoods direct-labeled.

## Decisions

- **nomic-embed-text over the qwen chat models** — purpose-built for embeddings,
  274 MB, runs fast on CPU. Texts use its `search_document:` prefix convention.
- **Embed text = name | class | categories | country | summary[:900]** — the
  categories anchor instruments with thin summaries; truncation keeps throughput up
  (summaries front-load the interesting part).
- **float16 storage** — halves the matrix to ~340 MB with no meaningful loss for
  cosine ranking. Loaded lazily; the feed only pays the RAM when it uses neighbours.
- **PCA, not UMAP/t-SNE, for the 2D view** — no new dependency, deterministic,
  seconds not hours at 226k points. The 2D view is for *humans*; all real
  navigation happens in the full 768-dim space, so the projection's blobbiness
  doesn't affect feed quality. Swap in UMAP later if the picture matters more.
- **Density = mean distance to 20 nearest in PCA-50 space** — brute-force k-NN at
  768 dims × 226k² is infeasible; PCA-50 preserves the neighbourhood structure
  well enough for a *crowding* signal. Low density = crowded trope ("yet another
  US small-cap biotech"), high = lonely outpost (feed treasure).
- **400 k-means neighbourhoods** — coarse enough to label, fine enough that a
  neighbourhood is a coherent theme. Labels derive from the dominant asset class,
  category, and country of members.
- **Ollama model store moved C: → D:** (`D:\ollama-models`, `OLLAMA_MODELS` user
  env var) — 10.8 GB freed from the small C: drive at the user's request; placed at
  the D: root rather than inside this project because the models are shared by all
  the user's AI projects, and deleting this project folder must not delete them.

## Debugging note (so it isn't relearned)

The map page rendered as a tiny blob in the corner for four straight attempts. The
cause was none of the plausible theories (bounds outliers, fit timing, browser
cache) but a CSS fact: **`<canvas>` is a replaced element — `position:absolute;
inset:0` does not stretch it**; it stays at its intrinsic 300×150 unless you set
`width:100%; height:100%` explicitly. Found by drawing the live variables onto the
canvas itself and reading them from a headless screenshot. Second lesson from the
same session: assigning `canvas.width` clears the canvas even when assigning the
same value — guard resize handlers, or a stray ResizeObserver callback erases the
frame between paint and screenshot.

## What plugs in next

`mf.explore()` is curiosity v0 (sparse-bias only). The real ordering upgrade:
**rabbit-hole** = long dwell on card X → next card from `mf.neighbors(X)`;
**whiplash** = next card maximizes map distance from X; **taste model** = learn
per-neighbourhood dwell scores from events.jsonl, then Thompson-sample
neighbourhoods (explore where the model is unsure about *you*). All of these are
a few lines each now that the map exists.

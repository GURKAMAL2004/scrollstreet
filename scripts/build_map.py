"""Build THE MAP: an embedding manifold over every symbol with a description.

Two phases (both idempotent / resumable):

  embed   - every symbol's text -> 768-dim vector via local Ollama (nomic-embed-text).
            Progress saved in chunks of 2048 to data/embeddings/chunks/; re-running
            skips finished chunks, so it can be interrupted freely.
  finish  - merge chunks -> data/embeddings.parquet (float16), then compute the map
            structure -> data/map_meta.parquet:
              x, y          2D layout (PCA) for the human-viewable map
              density       mean cosine distance to 20 nearest neighbours in PCA-50
                            space (low = crowded neighbourhood, high = lonely outpost)
              cluster       one of 400 k-means neighbourhoods
            and data/meta/map_clusters.json: per-cluster label from dominant categories.

Run:  .venv\\Scripts\\python.exe scripts\\build_map.py            (embed + finish)
      .venv\\Scripts\\python.exe scripts\\build_map.py finish     (skip to finish)
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import dataset as ds  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
MODEL = "nomic-embed-text"
CHUNK = 2048
BATCH = 64
CHUNKS_DIR = ROOT / "data" / "embeddings" / "chunks"
EMB_PATH = ROOT / "data" / "embeddings.parquet"
META_PATH = ROOT / "data" / "map_meta.parquet"
CLUSTERS_PATH = ROOT / "data" / "meta" / "map_clusters.json"
N_CLUSTERS = 400
KNN = 20


def corpus() -> pd.DataFrame:
    """Every symbol with a summary (delisted included - dead companies are content)."""
    c = ds.catalog()
    rows = c[c["has_summary"]].copy()
    rows = rows.sort_values(["asset_class", "symbol"]).reset_index(drop=True)
    return rows


def text_for(row, summaries: dict[str, pd.Series]) -> str:
    summary = summaries[row.asset_class].get(row.symbol, "")
    parts = [
        str(row.name_) if isinstance(row.name_, str) else "",
        row.asset_class,
        " / ".join(str(v) for v in (row.category_l1, row.category_l2, row.category_l3) if isinstance(v, str)),
        str(row.country) if isinstance(row.country, str) else "",
        str(summary)[:900],
    ]
    return "search_document: " + " | ".join(p for p in parts if p)


def embed_batch(texts: list[str]) -> list[list[float]]:
    for attempt in range(3):
        try:
            response = requests.post(
                f"{OLLAMA}/api/embed",
                json={"model": MODEL, "input": texts, "keep_alive": "60m"},
                timeout=300,
            )
            response.raise_for_status()
            return response.json()["embeddings"]
        except (requests.RequestException, KeyError) as error:
            if attempt == 2:
                raise
            print(f"  retry after error: {error}")
            time.sleep(5)
    return []


def phase_embed() -> None:
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    rows = corpus()
    rows = rows.rename(columns={"name": "name_"})  # .name clashes with tuple attr
    summaries = {
        cls: ds.load(cls, columns=["summary"])["summary"]
        for cls in rows["asset_class"].unique()
    }
    total_chunks = (len(rows) + CHUNK - 1) // CHUNK
    print(f"corpus: {len(rows):,} symbols -> {total_chunks} chunks of {CHUNK}")

    t_start = time.perf_counter()
    for chunk_index in range(total_chunks):
        out = CHUNKS_DIR / f"chunk_{chunk_index:05d}.parquet"
        if out.exists():
            continue
        part = rows.iloc[chunk_index * CHUNK : (chunk_index + 1) * CHUNK]
        texts = [text_for(r, summaries) for r in part.itertuples(index=False)]
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            vectors.extend(embed_batch(texts[i : i + BATCH]))
        pd.DataFrame(
            {
                "symbol": part["symbol"].to_numpy(),
                "asset_class": part["asset_class"].to_numpy(),
                "vec": [np.asarray(v, dtype=np.float16) for v in vectors],
            }
        ).to_parquet(out, engine="pyarrow", compression="zstd")
        done = sum(1 for _ in CHUNKS_DIR.glob("chunk_*.parquet"))
        rate = done * CHUNK / max(time.perf_counter() - t_start, 1)
        eta_min = (total_chunks - done) * CHUNK / max(rate, 1) / 60
        print(f"chunk {chunk_index + 1}/{total_chunks} done | ~{rate:,.0f} texts/s cum | eta {eta_min:,.0f} min")


def phase_finish() -> None:
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.decomposition import PCA
    from sklearn.neighbors import NearestNeighbors

    files = sorted(CHUNKS_DIR.glob("chunk_*.parquet"))
    print(f"merging {len(files)} chunks ...")
    parts = [pd.read_parquet(f) for f in files]
    merged = pd.concat(parts, ignore_index=True)
    merged.to_parquet(EMB_PATH, engine="pyarrow", compression="zstd")

    matrix = np.stack(merged["vec"].to_numpy()).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    print(f"matrix: {matrix.shape}, {matrix.nbytes / 1e6:.0f} MB in RAM (float32)")

    print("PCA -> 50 dims (working space) and 2 dims (viewing space) ...")
    pca50 = PCA(n_components=50, random_state=0).fit(matrix[:: max(1, len(matrix) // 40000)])
    work = pca50.transform(matrix)
    xy = work[:, :2]

    print(f"k-NN density (k={KNN}) in PCA-50 space ...")
    knn = NearestNeighbors(n_neighbors=KNN + 1).fit(work)
    distances, _ = knn.kneighbors(work)
    density = distances[:, 1:].mean(axis=1)  # mean distance to 20 nearest, self excluded

    print(f"k-means -> {N_CLUSTERS} neighbourhoods ...")
    km = MiniBatchKMeans(n_clusters=N_CLUSTERS, random_state=0, batch_size=4096, n_init=3)
    clusters = km.fit_predict(work)

    meta = pd.DataFrame(
        {
            "symbol": merged["symbol"],
            "asset_class": merged["asset_class"],
            "x": xy[:, 0].astype(np.float32),
            "y": xy[:, 1].astype(np.float32),
            "density": density.astype(np.float32),
            "cluster": clusters.astype(np.int16),
        }
    )
    meta.to_parquet(META_PATH, engine="pyarrow", compression="zstd")

    # Human-readable cluster labels from dominant catalog categories
    catalog = ds.catalog()[["symbol", "asset_class", "category_l1", "category_l2", "country"]]
    labeled = meta.merge(catalog, on=["symbol", "asset_class"], how="left")
    labels = {}
    for cid, grp in labeled.groupby("cluster"):
        top = [
            str(grp[col].mode().iloc[0])
            for col in ("asset_class", "category_l2", "country")
            if grp[col].notna().any() and not grp[col].mode().empty
        ]
        labels[int(cid)] = {"label": " · ".join(top), "size": int(len(grp))}
    CLUSTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CLUSTERS_PATH, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=1)

    print(f"\nmap built: {len(meta):,} points")
    print(f"  {EMB_PATH.name}  {EMB_PATH.stat().st_size / 1e6:.0f} MB")
    print(f"  {META_PATH.name} {META_PATH.stat().st_size / 1e6:.1f} MB")
    print(f"  {CLUSTERS_PATH.name} ({N_CLUSTERS} neighbourhood labels)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "embed"):
        phase_embed()
    if mode in ("all", "finish"):
        phase_finish()

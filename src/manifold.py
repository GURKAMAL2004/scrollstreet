"""THE MAP's API - navigate the embedding manifold of the financial universe.

Requires scripts/build_map.py to have run. Everything is local and lazy:
map_meta (a few MB) loads on first use; the full vector matrix (~340 MB in
float16) loads only when a neighbour query actually needs it.

    from src import manifold as mf

    mf.where("TSLA")                    # coordinates, neighbourhood, crowding
    mf.neighbors("TSLA", k=10)          # nearest points on the map
    mf.lonely(10)                       # the loneliest outposts of the map
    mf.explore(seen={"TSLA"}, n=5)      # sparse-region sampler for the feed
"""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
EMB_PATH = ROOT / "data" / "embeddings.parquet"
META_PATH = ROOT / "data" / "map_meta.parquet"
CLUSTERS_PATH = ROOT / "data" / "meta" / "map_clusters.json"

_state: dict = {}


@lru_cache(maxsize=1)
def meta() -> pd.DataFrame:
    m = pd.read_parquet(META_PATH)
    m["row"] = np.arange(len(m))
    return m


@lru_cache(maxsize=1)
def clusters() -> dict:
    with open(CLUSTERS_PATH, encoding="utf-8") as f:
        return {int(k): v for k, v in json.load(f).items()}


def _matrix() -> np.ndarray:
    """Full normalized embedding matrix, float16, loaded once on demand."""
    if "matrix" not in _state:
        vectors = pd.read_parquet(EMB_PATH, columns=["vec"])["vec"]
        matrix = np.stack(vectors.to_numpy()).astype(np.float16)
        norms = np.linalg.norm(matrix.astype(np.float32), axis=1, keepdims=True) + 1e-9
        _state["matrix"] = (matrix / norms).astype(np.float16)
    return _state["matrix"]


def _row_of(symbol: str, asset_class: str | None = None) -> int | None:
    m = meta()
    hits = m[m["symbol"] == symbol]
    if asset_class:
        hits = hits[hits["asset_class"] == asset_class]
    return None if hits.empty else int(hits["row"].iloc[0])


def _cosine_to(row: int) -> np.ndarray:
    matrix = _matrix()
    query = matrix[row].astype(np.float32)
    scores = np.empty(len(matrix), dtype=np.float32)
    step = 20000
    for start in range(0, len(matrix), step):
        scores[start : start + step] = matrix[start : start + step].astype(np.float32) @ query
    return scores


def where(symbol: str, asset_class: str | None = None) -> dict | None:
    """One symbol's place on the map: coordinates, neighbourhood, crowding."""
    row = _row_of(symbol, asset_class)
    if row is None:
        return None
    r = meta().iloc[row]
    density_rank = float((meta()["density"] < r["density"]).mean())
    return {
        "symbol": r["symbol"],
        "asset_class": r["asset_class"],
        "x": float(r["x"]),
        "y": float(r["y"]),
        "cluster": int(r["cluster"]),
        "neighbourhood": clusters().get(int(r["cluster"]), {}).get("label", "?"),
        "neighbourhood_size": clusters().get(int(r["cluster"]), {}).get("size"),
        "loneliness_percentile": round(density_rank * 100),  # 100 = most remote
    }


def neighbors(symbol: str, k: int = 10, asset_class: str | None = None) -> pd.DataFrame:
    """The k nearest points on the map (cosine similarity in full 768-dim space)."""
    row = _row_of(symbol, asset_class)
    if row is None:
        return pd.DataFrame()
    scores = _cosine_to(row)
    scores[row] = -1
    top = np.argpartition(scores, -k)[-k:]
    top = top[np.argsort(scores[top])[::-1]]
    result = meta().iloc[top][["symbol", "asset_class", "cluster"]].copy()
    result["similarity"] = scores[top].round(3)
    result["neighbourhood"] = [
        clusters().get(int(c), {}).get("label", "?") for c in result["cluster"]
    ]
    return result.reset_index(drop=True)


def lonely(n: int = 10) -> pd.DataFrame:
    """The most remote outposts of the map - the feed's buried treasure."""
    m = meta().nlargest(n, "density")[["symbol", "asset_class", "cluster", "density"]]
    m = m.copy()
    m["neighbourhood"] = [clusters().get(int(c), {}).get("label", "?") for c in m["cluster"]]
    return m.reset_index(drop=True)


def explore(seen: set[str] | None = None, n: int = 1, sparse_bias: float = 2.0) -> pd.DataFrame:
    """Sample map points weighted toward sparse (lonely) regions - curiosity v0.

    sparse_bias > 1 favours remote points; 0 = uniform. The future taste model
    will replace this weighting with 'where am I most unsure about the user'.
    """
    m = meta()
    pool = m if not seen else m[~m["symbol"].isin(seen)]
    weights = np.power(pool["density"].to_numpy(dtype=np.float64), sparse_bias)
    weights /= weights.sum()
    picks = pool.sample(min(n, len(pool)), weights=weights)
    return picks[["symbol", "asset_class", "cluster", "density"]].reset_index(drop=True)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    sym = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    print(json.dumps(where(sym), indent=2, ensure_ascii=False))
    print(neighbors(sym, k=8).to_string())
    print("\nloneliest outposts:")
    print(lonely(5).to_string())

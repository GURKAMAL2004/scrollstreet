"""Ordering engine v2: filtered random walk, lonely-outpost bias, neighbourhood seeds.

The feed builder passes constraints; this module turns them into a candidate
pool and samples the next card. Modes:

  random  - uniform draw over the (filtered) usable universe
  lonely  - density-weighted draw favouring the map's remote corners
  seed    - the pool is a symbol's semantic neighbourhood on the map

Map-based modes degrade gracefully to filtered random if the map isn't built.
"""

import random

import numpy as np

from src import dataset as ds


def _usable_pool(classes=None, country=None, l1=None):
    pool = ds.catalog()
    pool = pool[pool["has_summary"] & ~pool["delisted"]]
    if classes:
        pool = pool[pool["asset_class"].isin(classes)]
    if country:
        pool = pool[pool["country"] == country]
    if l1:
        pool = pool[pool["category_l1"] == l1]
    return pool


def _seed_pool(seed: str, seed_class: str | None):
    """A symbol's semantic neighbourhood, cross-listings collapsed."""
    from src import manifold as mf

    hood = mf.neighbors(seed, k=400, asset_class=seed_class or None)
    if hood.empty:
        return None
    names = ds.catalog()[["symbol", "asset_class", "name", "has_summary", "delisted"]]
    hood = hood.merge(names, on=["symbol", "asset_class"], how="left")
    seed_record = ds.get(seed, asset_class=seed_class)
    seed_name = (seed_record or {}).get("name")
    hood = hood[hood["has_summary"] & ~hood["delisted"]]
    if seed_name:
        hood = hood[hood["name"] != seed_name]          # skip the seed's own cross-listings
    hood = hood.drop_duplicates("name")                  # one listing per company
    return hood


def next_symbols(
    seen: set[str],
    n: int = 1,
    classes: list[str] | None = None,
    country: str | None = None,
    l1: str | None = None,
    mode: str = "random",
    seed: str | None = None,
    seed_class: str | None = None,
) -> list[tuple[str, str]]:
    """Return the next (symbol, asset_class) picks for a session."""
    rng = random.randrange(2**32)

    if seed:
        try:
            hood = _seed_pool(seed, seed_class)
        except FileNotFoundError:
            hood = None
        if hood is not None and not hood.empty:
            hood = hood[~hood["symbol"].isin(seen | {seed})]
            if not hood.empty:
                # nearest-first with a little shuffle so sessions differ
                take = hood.head(max(n * 6, 12)).sample(
                    min(n, len(hood)), random_state=rng
                )
                return list(zip(take["symbol"], take["asset_class"]))
        # fall through to filtered random if the neighbourhood is exhausted

    pool = _usable_pool(classes, country, l1)
    if seen:
        pool = pool[~pool["symbol"].isin(seen)]
    if pool.empty:
        return []

    if mode == "lonely":
        try:
            from src import manifold as mf

            density = mf.meta()[["symbol", "asset_class", "density"]]
            pool = pool.merge(density, on=["symbol", "asset_class"], how="left")
            weights = np.power(
                pool["density"].fillna(pool["density"].median()).to_numpy(dtype=float), 2.0
            )
            rows = pool.sample(min(n, len(pool)), weights=weights, random_state=rng)
            return list(zip(rows["symbol"], rows["asset_class"]))
        except FileNotFoundError:
            pass  # map not built - plain random below

    rows = pool.sample(min(n, len(pool)), random_state=rng)
    return list(zip(rows["symbol"], rows["asset_class"]))

"""Ordering engine v1: pure random walk over the usable universe.

This is deliberately the dumbest strategy (roadmap Step 3 adds rabbit-hole,
whiplash, serpentine, bandit blending). Interface is already the final one:
given what a session has seen, return the next symbols to show.
"""

import random

from src import dataset as ds


def next_symbols(seen: set[str], n: int = 1) -> list[tuple[str, str]]:
    """Uniform random draw from feed-usable symbols not yet seen this session."""
    pool = ds.catalog()
    pool = pool[pool["has_summary"] & ~pool["delisted"]]
    if seen:
        pool = pool[~pool["symbol"].isin(seen)]
    if pool.empty:
        return []
    rows = pool.sample(min(n, len(pool)), random_state=random.randrange(2**32))
    return list(zip(rows["symbol"], rows["asset_class"]))

"""Finance LLM — entry point / dataset smoke test.

Run:  .venv\\Scripts\\python.exe main.py

Proves the local FinanceDatabase arrangement works end-to-end with zero
network access: unified catalog, per-class loads, the upstream package's
query API, and a random card-ready record like the future feed would draw.
"""

import time

from src import dataset as ds


def main() -> None:
    t0 = time.perf_counter()
    pool = ds.catalog()
    print(f"catalog: {len(pool):,} symbols loaded in {time.perf_counter() - t0:.2f}s")
    print(pool["asset_class"].value_counts().to_string(), "\n")

    equities = ds.load_fd("equities")
    dutch_insurance = equities.select(
        country="Netherlands", industry="Insurance", market="Euronext Amsterdam"
    )
    print(f"upstream .select() works offline -> {len(dutch_insurance)} Dutch insurers:")
    print(dutch_insurance["name"].to_string(), "\n")

    card = ds.sample(1, asset_class="equities").iloc[0]
    record = ds.get(card["symbol"], asset_class="equities")
    print("random card-ready record (what the feed would hand the LLM):")
    for key in ("symbol", "name", "sector", "industry", "country", "market_cap"):
        print(f"  {key:>11}: {record.get(key)}")
    summary = str(record.get("summary") or "")
    print(f"      summary: {summary[:220]}...")


if __name__ == "__main__":
    main()

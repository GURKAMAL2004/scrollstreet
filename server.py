"""Feed server - stdlib only, binds to 127.0.0.1, zero external calls except Ollama.

Run:   .venv\\Scripts\\python.exe server.py
Open:  http://127.0.0.1:8765

Endpoints:
  GET  /               the scroll UI (web/index.html)
  GET  /api/next?session=<id>   pick next symbol (random walk), generate card
  POST /api/event       append interaction event to data/feed/events.jsonl
  GET  /api/health      model + universe info
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src import cards, ordering
from src import dataset as ds

ROOT = Path(__file__).resolve().parent
EVENTS = ROOT / "data" / "feed" / "events.jsonl"
MAX_CARDS = 25

CLASS_ORDER = ["equities", "indices", "funds", "etfs", "cryptos", "currencies", "moneymarkets"]
# Validated categorical palette (dataviz method, dark surface #0b0d10, order chosen
# for max adjacent CVD separation - worst pair deltaE 23.6)
CLASS_COLORS = ["#3987e5", "#199e70", "#c98500", "#9085e9", "#008300", "#e66767", "#d55181"]

_sessions: dict[str, set[str]] = {}
_lock = threading.Lock()
_log_lock = threading.Lock()
_map_cache: bytes | None = None


def map_payload() -> bytes | None:
    """JSON for the map page, built once and cached (needs build_map.py output)."""
    global _map_cache
    if _map_cache is not None:
        return _map_cache
    from src import manifold as mf

    if not mf.META_PATH.exists():
        return None
    meta = mf.meta()
    hoods = mf.clusters()
    names = ds.catalog()[["symbol", "asset_class", "name"]]
    merged = meta.merge(names, on=["symbol", "asset_class"], how="left")
    class_index = merged["asset_class"].map({c: i for i, c in enumerate(CLASS_ORDER)})

    top_clusters = meta.groupby("cluster").size().nlargest(14).index
    labels = []
    for cid in top_clusters:
        grp = meta[meta["cluster"] == cid]
        text = hoods.get(int(cid), {}).get("label", "")
        labels.append(
            {"x": round(float(grp["x"].median()), 3), "y": round(float(grp["y"].median()), 3),
             "text": text[:34]}
        )
    payload = {
        "classes": CLASS_ORDER,
        "colors": CLASS_COLORS,
        "counts": [int((merged["asset_class"] == c).sum()) for c in CLASS_ORDER],
        "hoods": {str(k): v["label"] for k, v in hoods.items()},
        "labels": labels,
        # robust bounds: a handful of PCA outliers must not squeeze the cloud into a corner
        "bounds": {
            "xmin": float(meta["x"].quantile(0.005)), "xmax": float(meta["x"].quantile(0.995)),
            "ymin": float(meta["y"].quantile(0.005)), "ymax": float(meta["y"].quantile(0.995)),
        },
        "points": {
            "x": [round(float(v), 3) for v in merged["x"]],
            "y": [round(float(v), 3) for v in merged["y"]],
            "c": [int(v) for v in class_index],
            "h": [int(v) for v in merged["cluster"]],
            "sym": merged["symbol"].tolist(),
            "name": [str(n)[:44] if isinstance(n, str) else "" for n in merged["name"]],
        },
    }
    _map_cache = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _map_cache


def log_event(event: dict) -> None:
    event["server_ts"] = time.time()
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with _log_lock, open(EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


_filters_cache: dict | None = None


def filters_payload() -> dict:
    """Everything the feed-builder UI needs to render its choices."""
    global _filters_cache
    if _filters_cache is None:
        pool = ds.catalog()
        pool = pool[pool["has_summary"] & ~pool["delisted"]]
        counts = pool["asset_class"].value_counts()
        _filters_cache = {
            "total": int(len(pool)),
            "model": cards.pick_model(),
            "classes": [
                {"name": c, "count": int(counts.get(c, 0))}
                for c in CLASS_ORDER
                if counts.get(c, 0) > 0
            ],
            "countries": pool["country"].value_counts().head(60).index.tolist(),
            "categories": pool["category_l1"].value_counts().head(40).index.tolist(),
        }
    return _filters_cache


def search_universe(query: str) -> list[dict]:
    """Name/symbol search over everything with a story, map coords included."""
    c = ds.catalog()
    c = c[c["has_summary"]]
    mask = c["name"].str.contains(query, case=False, na=False) | c[
        "symbol"
    ].str.upper().str.startswith(query.upper())
    hits = c[mask].copy()
    if hits.empty:
        return []
    hits["_len"] = hits["symbol"].str.len()
    hits = hits.sort_values("_len").drop_duplicates("name").head(20)
    try:
        from src import manifold as mf

        coords = mf.meta()[["symbol", "asset_class", "x", "y"]]
        hits = hits.merge(coords, on=["symbol", "asset_class"], how="left")
    except FileNotFoundError:
        hits["x"] = None
        hits["y"] = None
    out = []
    for row in hits.itertuples(index=False):
        out.append(
            {
                "symbol": row.symbol,
                "name": row.name if isinstance(row.name, str) else row.symbol,
                "asset_class": row.asset_class,
                "country": row.country if isinstance(row.country, str) else None,
                "category": next(
                    (v for v in (row.category_l3, row.category_l1) if isinstance(v, str)),
                    None,
                ),
                "x": round(float(row.x), 4) if row.x == row.x and row.x is not None else None,
                "y": round(float(row.y), 4) if row.y == row.y and row.y is not None else None,
            }
        )
    return out


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, name: str) -> None:
        body = (ROOT / "web" / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            self._send_file("home.html")
        elif url.path == "/feed":
            self._send_file("index.html")
        elif url.path == "/api/next":
            params = parse_qs(url.query)

            def one(key):
                value = params.get(key, [None])[0]
                return value or None

            session = one("session") or "anon"
            classes = one("classes")
            filters = {
                "classes": classes.split(",") if classes else None,
                "country": one("country"),
                "l1": one("l1"),
                "mode": one("mode") or "random",
                "seed": one("seed"),
                "seed_class": one("seed_class"),
            }
            with _lock:
                seen = _sessions.setdefault(session, set())
                index = len(seen)
                if index >= MAX_CARDS:
                    self._send_json({"done": True, "index": index})
                    return
                picks = ordering.next_symbols(seen, n=1, **filters)
                if not picks:
                    self._send_json({"done": True, "index": index})
                    return
                symbol, asset_class = picks[0]
                seen.add(symbol)
            card = cards.generate_card(symbol, asset_class)
            card["index"] = index + 1
            card["of"] = MAX_CARDS
            strategy = f"seed:{filters['seed']}" if filters["seed"] else filters["mode"]
            log_event(
                {
                    "type": "card_generated",
                    "session": session,
                    "index": index + 1,
                    "symbol": symbol,
                    "asset_class": asset_class,
                    "strategy": strategy,
                    "filters": {k: v for k, v in filters.items() if v and k != "mode"},
                    "model": card["model"],
                    "gen_ms": card["gen_ms"],
                }
            )
            self._send_json(card)
        elif url.path == "/api/search":
            query = parse_qs(url.query).get("q", [""])[0].strip()
            self._send_json(search_universe(query) if len(query) >= 2 else [])
        elif url.path == "/api/filters":
            self._send_json(filters_payload())
        elif url.path == "/map":
            body = (ROOT / "web" / "map.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/map":
            body = map_payload()
            if body is None:
                self._send_json({"error": "map not built - run scripts/build_map.py"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/health":
            self._send_json(
                {
                    "model": cards.pick_model(),
                    "universe": int(len(ds.catalog())),
                    "max_cards": MAX_CARDS,
                }
            )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path == "/api/event":
            length = int(self.headers.get("Content-Length", 0))
            try:
                event = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                event = {"type": "malformed"}
            log_event(event)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # quiet console; events.jsonl is the log
        pass


def main() -> None:
    # Windows lets multiple processes bind the same port (SO_REUSEADDR quirk),
    # so orphaned servers stack up silently - refuse to start a second copy
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2)
        print("ScrollStreet already running at http://127.0.0.1:8765 - exiting")
        return
    except OSError:
        pass

    ds.catalog()  # warm the pool before first request
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print(f"feed running -> http://127.0.0.1:8765  (model: {cards.pick_model()})")
    server.serve_forever()


if __name__ == "__main__":
    main()

"""Card generator: database record + computed math -> local LLM -> story card.

The contract with the LLM: it may only use numbers present in the DATA/MATH
sections of the prompt (all computed locally in src/enrich.py). If Ollama is
down or the model misbehaves, a template fallback card is produced so the feed
never stalls.

Test one card from the terminal:
    .venv\\Scripts\\python.exe -m src.cards TSLA equities
"""

import json
import os
import time

import requests

from src import dataset as ds
from src.enrich import LEVEL_LABELS, enrich

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# Fast model first: feed cadence beats prose quality. Override with FEED_MODEL.
MODEL_PREFERENCE = ["qwen2.5:1.5b-instruct", "qwen2.5:14b-instruct", "qwen2.5", "llama3", "gemma", "phi", "mistral"]

SYSTEM_PROMPT = """You write cards for a personal finance-discovery feed (like TikTok, but every post is one financial instrument). You receive raw DATA about one instrument and MATH computed from a 353,000-symbol database.

Respond with ONLY a JSON object:
{
 "headline": "<= 9 words, intriguing, no ticker symbol in it",
 "hook": "one punchy sentence that makes scrolling past feel like a loss",
 "story": "90-140 words. Concrete and specific. Weave in AT LEAST 3 numbers taken verbatim from DATA or MATH. Explain what one of those numbers actually means for context.",
 "wild_fact": "the single most surprising thing here, one sentence",
 "open_question": "an unresolved question that lingers, one sentence",
 "vibe": "one of: witty | dark | educational | awe",
 "emoji": "one emoji"
}

HARD RULES:
- Every number you write MUST appear in DATA or MATH. Never invent prices, returns, dates, or statistics.
- MATH numbers are database counts and percentages. NEVER present them as dollars, assets under management, revenue, or prices — nothing here is money.
- No investment advice, no predictions.
- If the instrument is delisted, the story is about its death.
- Plain language; a curious teenager should get it."""


def _available_models() -> list[str]:
    try:
        tags = requests.get(f"{OLLAMA}/api/tags", timeout=3).json()
        return [
            m["name"] for m in tags.get("models", []) if "cloud" not in m["name"]
        ]
    except requests.RequestException:
        return []


def pick_model() -> str | None:
    forced = os.environ.get("FEED_MODEL")
    if forced:
        return forced
    models = _available_models()
    for preference in MODEL_PREFERENCE:
        for model in models:
            if model.startswith(preference):
                return model
    return models[0] if models else None


def _build_prompt(record: dict, math: dict) -> str:
    labels = LEVEL_LABELS.get(record["asset_class"], (None, None, None))
    skip = {"summary", "isin", "cusip", "figi", "composite_figi", "shareclass_figi", "mic", "zipcode"}
    data_lines = [
        f"- {key}: {value}"
        for key, value in record.items()
        if key not in skip and value not in (None, "", False)
    ]
    summary = str(record.get("summary") or "")[:600]
    math_lines = [f"- {key}: {value}" for key, value in math["stats"].items()]
    formula_lines = [f"- {line}" for line in math["formulas"]]
    return (
        "DATA (fields of this instrument):\n" + "\n".join(data_lines)
        + f"\n\nDESCRIPTION (may be truncated):\n{summary}"
        + "\n\nMATH (counts and percentages computed locally from the full 353k-symbol database - these are NOT dollar amounts):\n"
        + "\n".join(math_lines)
        + "\n\nHow the MATH was derived:\n" + "\n".join(formula_lines)
        + f"\n\nCategory levels mean: {', '.join(l for l in labels if l)}."
        + "\nWrite the card JSON now."
    )


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1])


def _fallback_card(record: dict, math: dict) -> dict:
    name = record.get("name") or record["symbol"]
    stats = list(math["stats"].items())
    highlight = stats[1] if len(stats) > 1 else ("facts on file", len(record))
    return {
        "headline": f"Meet {name}",
        "hook": f"One of {highlight[1]} in its corner of the market.",
        "story": (
            f"{name} ({record['symbol']}) is a {record.get('category_l3') or record.get('category_l1') or record['asset_class']} "
            f"entry in the {record['asset_class']} universe. "
            + " ".join(f"{k}: {v}." for k, v in stats[:4])
        ),
        "wild_fact": f"{highlight[0]}: {highlight[1]}.",
        "open_question": "What would you find one click deeper into this niche?",
        "vibe": "educational",
        "emoji": "📊",
    }


def generate_card(symbol: str, asset_class: str, model: str | None = None) -> dict:
    """Full pipeline for one card. Never raises; falls back to a template."""
    record = ds.get(symbol, asset_class=asset_class) or {
        "symbol": symbol,
        "asset_class": asset_class,
    }
    math = enrich(symbol, asset_class)
    model = model or pick_model()

    card, used_model, t0 = None, "fallback", time.perf_counter()
    if model:
        try:
            response = requests.post(
                f"{OLLAMA}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "keep_alive": "30m",
                    "options": {"temperature": 0.9, "num_predict": 600},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": _build_prompt(record, math)},
                    ],
                },
                timeout=120,
            )
            response.raise_for_status()
            card = _parse_json(response.json()["message"]["content"])
            used_model = model
        except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError):
            card = None
    if not isinstance(card, dict) or "story" not in card:
        card = _fallback_card(record, math)
        used_model = "fallback"

    return {
        "symbol": record["symbol"],
        "name": record.get("name"),
        "asset_class": asset_class,
        "country": record.get("country"),
        "exchange": record.get("exchange"),
        "market": record.get("market"),
        "market_cap": record.get("market_cap"),
        "website": record.get("website"),
        "delisted": bool(record.get("delisted") or False),
        "categories": [
            record.get(c)
            for c in ("sector", "industry_group", "industry", "category_group", "category", "family", "cryptocurrency")
            if record.get(c)
        ],
        "card": card,
        "stats": math["stats"],
        "model": used_model,
        "gen_ms": int((time.perf_counter() - t0) * 1000),
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1252
    sym = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    cls = sys.argv[2] if len(sys.argv) > 2 else "equities"
    print(json.dumps(generate_card(sym, cls), indent=2, ensure_ascii=False))

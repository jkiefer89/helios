"""Lightweight financial-news sentiment.

A compact lexicon scorer in the spirit of Loughran-McDonald: count polarized
finance terms, weight by intensity, normalize to [-1, 1]. No external services
or API keys required, so it works fully offline on bundled or pasted headlines.
"""
from __future__ import annotations

import re

# Intensity-weighted finance lexicon.
_POSITIVE = {
    "beat": 1.0, "beats": 1.0, "surge": 1.3, "surges": 1.3, "soar": 1.4, "soars": 1.4,
    "rally": 1.1, "rallies": 1.1, "upgrade": 1.2, "upgrades": 1.2, "outperform": 1.2,
    "growth": 0.8, "profit": 0.8, "profits": 0.8, "record": 1.0, "strong": 0.9,
    "bullish": 1.3, "gains": 0.9, "gain": 0.9, "jump": 1.0, "jumps": 1.0,
    "optimism": 1.0, "robust": 0.9, "boost": 0.9, "rebound": 1.0, "recovers": 0.9,
    "recovery": 0.9, "demand": 0.6, "raises": 0.8, "raised": 0.8, "expands": 0.6,
    "wins": 0.8, "approval": 0.9, "breakthrough": 1.2, "momentum": 0.7,
}
_NEGATIVE = {
    "miss": 1.0, "misses": 1.0, "plunge": 1.4, "plunges": 1.4, "slump": 1.2,
    "selloff": 1.3, "downgrade": 1.2, "downgrades": 1.2, "loss": 0.9, "losses": 0.9,
    "lawsuit": 1.0, "probe": 1.0, "investigation": 1.0, "bearish": 1.3, "weak": 0.9,
    "weakness": 0.9, "cut": 0.7, "cuts": 0.7, "slip": 0.8, "slips": 0.8,
    "fears": 1.0, "recession": 1.2, "default": 1.4, "recall": 1.1, "warning": 1.0,
    "fraud": 1.5, "decline": 0.9, "declines": 0.9, "drop": 0.9, "drops": 0.9,
    "tumble": 1.3, "tumbles": 1.3, "restrictions": 0.8, "antitrust": 0.9, "halt": 1.0,
    "crash": 1.5, "slashes": 1.1, "slashed": 1.1, "concerns": 0.7, "pressure": 0.7,
}
_NEGATORS = {"not", "no", "without", "despite", "fails", "fail"}
_TOKEN = re.compile(r"[a-zA-Z']+")


def score_text(text: str) -> float:
    """Net sentiment of one string in [-1, 1]."""
    tokens = _TOKEN.findall((text or "").lower())
    if not tokens:
        return 0.0
    score = 0.0
    for i, tok in enumerate(tokens):
        val = _POSITIVE.get(tok, 0.0) - _NEGATIVE.get(tok, 0.0)
        if val and i > 0 and tokens[i - 1] in _NEGATORS:
            val = -val  # "not strong" flips polarity
        score += val
    # Squash by token count so long headlines don't dominate.
    norm = score / (len(tokens) ** 0.5)
    return float(max(-1.0, min(1.0, norm / 2.0)))


def label(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def score_headlines(headlines: list[str]) -> dict:
    items = []
    for h in headlines or []:
        s = score_text(h)
        items.append({"headline": h, "score": round(s, 3), "label": label(s)})
    avg = sum(i["score"] for i in items) / len(items) if items else 0.0
    return {
        "items": items,
        "aggregate_score": round(float(avg), 3),
        "aggregate_label": label(avg),
        "count": len(items),
    }

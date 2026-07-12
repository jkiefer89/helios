"""Macro intelligence layer: Fed speak, White House policy, geopolitics.

Forecasting from more than price and fundamentals: this module ingests the
free, primary-source feeds that move markets —

  * Federal Reserve monetary-policy press releases and official speeches
    (federalreserve.gov RSS), scored on an intensity-weighted HAWK/DOVE
    lexicon (Loughran-McDonald spirit, same approach as engine.sentiment);
  * White House presidential actions (whitehouse.gov RSS), tagged with
    policy THEMES (trade, energy, healthcare, ...) and the equity sectors
    each theme touches;
  * a GEOPOLITICAL RISK index from GDELT's global news firehose (conflict/
    sanction/escalation lexicon over a fixed macro query);
  * the FOMC meeting calendar (static, published schedule) for event-risk
    proximity.

Everything here follows the house rules: a deterministic, lexicon-based core
(the AI copilot may narrate on top, but the numbers never depend on an LLM);
injectable HTTP for offline tests; TTL caches; and honest degradation — a
source that cannot be reached reports ``available: False`` instead of a
fabricated calm. Scores are CONTEXT and CONVICTION DAMPERS, transparently
labeled — they never fabricate a return forecast.
"""
from __future__ import annotations

import calendar
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any

_UA = "Helios Research Terminal (research use; contact via repo owner)"
_MAX_RESP_BYTES = 4 * 1024 * 1024
_SNAPSHOT_TTL_S = 30 * 60.0
_FEED_ITEM_CAP = 12
_GDELT_MAX_RECORDS = 40

_HTTP = None
_LOCK = threading.RLock()
# Serializes the expensive cold-snapshot BUILD (single-flight); _LOCK guards
# only the cheap cache reads/writes so they never queue behind a slow build.
_FETCH_LOCK = threading.Lock()
_SNAPSHOT_CACHE: list = [0.0, None]   # [monotonic_ts, snapshot]

FED_MONETARY_FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
FED_SPEECHES_FEED = "https://www.federalreserve.gov/feeds/speeches.xml"
WH_ACTIONS_FEED = "https://www.whitehouse.gov/presidential-actions/feed/"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# sourcelang is a QUERY OPERATOR in the GDELT DOC 2.0 API — the standalone URL
# parameter is ignored and let non-English articles into the English-lexicon
# scorer (review finding, verified live).
GDELT_GEOPOLITICS_QUERY = (
    '(sanctions OR tariff OR tariffs OR war OR invasion OR missile OR ceasefire '
    'OR blockade OR "export controls" OR escalation OR "nuclear test") '
    'sourcelang:english'
)

# --------------------------------------------------------------------------- #
# FOMC calendar — the published schedule (static data, update annually).
# --------------------------------------------------------------------------- #
FOMC_MEETINGS_2026 = (
    ("2026-01-27", "2026-01-28"),
    ("2026-03-17", "2026-03-18"),
    ("2026-04-28", "2026-04-29"),
    ("2026-06-16", "2026-06-17"),
    ("2026-07-28", "2026-07-29"),
    ("2026-09-15", "2026-09-16"),
    ("2026-10-27", "2026-10-28"),
    ("2026-12-08", "2026-12-09"),
)
FOMC_IMMINENT_DAYS = 3   # decision-day proximity that warrants a conviction damper

# --------------------------------------------------------------------------- #
# Lexicons — intensity-weighted, deterministic, auditable.
# --------------------------------------------------------------------------- #
_HAWKISH = {
    "hawkish": 1.5, "tighten": 1.2, "tightening": 1.2, "restrictive": 1.2,
    "hike": 1.3, "hikes": 1.3, "raise": 0.8, "raising": 0.8, "raised": 0.6,
    "inflationary": 1.0, "overheating": 1.2, "persistent": 0.8, "elevated": 0.8,
    "vigilant": 0.9, "upside": 0.5, "firm": 0.5, "higher": 0.5, "longer": 0.4,
    "reaccelerating": 1.1, "sticky": 1.0, "unacceptably": 1.3,
}
_DOVISH = {
    "dovish": 1.5, "cut": 1.3, "cuts": 1.3, "cutting": 1.3, "ease": 1.1,
    "easing": 1.1, "accommodative": 1.2, "patient": 0.8, "progress": 0.7,
    "cooling": 0.9, "moderating": 0.8, "moderated": 0.8, "disinflation": 1.2,
    "softening": 0.9, "downside": 0.6, "lower": 0.5, "normalize": 0.7,
    "normalization": 0.7, "gradual": 0.5, "slack": 0.8, "weakening": 0.9,
}
_GEO_RISK = {
    "war": 1.3, "invasion": 1.5, "missile": 1.2, "missiles": 1.2, "strike": 1.0,
    "strikes": 1.0, "attack": 1.1, "attacks": 1.1, "escalation": 1.3,
    "escalates": 1.3, "sanctions": 1.0, "blockade": 1.3, "conflict": 1.0,
    "nuclear": 1.3, "troops": 1.0, "mobilization": 1.2, "hostilities": 1.2,
    "retaliation": 1.2, "retaliate": 1.2, "embargo": 1.1, "seize": 1.0,
    "shelling": 1.2, "drone": 0.9, "drones": 0.9, "warship": 1.0, "coup": 1.3,
}
_GEO_CALM = {
    "ceasefire": 1.2, "truce": 1.2, "peace": 1.0, "deal": 0.6, "agreement": 0.7,
    "de-escalation": 1.3, "talks": 0.7, "negotiations": 0.8, "resolution": 0.8,
}
_TOKEN = re.compile(r"[a-zA-Z'-]+")

# Policy themes: keyword -> (theme, affected sectors in macro.sector_anchor vocab).
_POLICY_THEMES = {
    "tariff": ("trade", ("industrials", "materials", "consumer discretionary", "technology")),
    "tariffs": ("trade", ("industrials", "materials", "consumer discretionary", "technology")),
    "trade": ("trade", ("industrials", "materials", "consumer discretionary")),
    "export": ("trade", ("technology", "industrials")),
    "sanction": ("trade", ("energy", "financials", "industrials")),
    "sanctions": ("trade", ("energy", "financials", "industrials")),
    "energy": ("energy", ("energy", "utilities")),
    "oil": ("energy", ("energy",)),
    "drilling": ("energy", ("energy",)),
    "climate": ("energy", ("energy", "utilities", "industrials")),
    "drug": ("healthcare", ("healthcare",)),
    "medicare": ("healthcare", ("healthcare",)),
    "medicaid": ("healthcare", ("healthcare",)),
    "健康": ("healthcare", ("healthcare",)),
    "tax": ("fiscal", ("financials", "consumer discretionary")),
    "taxes": ("fiscal", ("financials", "consumer discretionary")),
    "spending": ("fiscal", ("industrials", "healthcare")),
    "infrastructure": ("fiscal", ("industrials", "materials")),
    "antitrust": ("regulation", ("technology", "communication services")),
    "regulation": ("regulation", ("financials", "technology")),
    "deregulation": ("regulation", ("financials", "energy")),
    "crypto": ("regulation", ("financials", "technology")),
    "semiconductor": ("technology", ("technology",)),
    "chips": ("technology", ("technology",)),
    "artificial": ("technology", ("technology", "communication services")),
    "immigration": ("labor", ("industrials", "consumer discretionary")),
    "labor": ("labor", ("industrials", "consumer discretionary")),
    "tiktok": ("regulation", ("communication services", "technology")),
    "defense": ("defense", ("industrials",)),
    "military": ("defense", ("industrials",)),
}


def set_http(fn) -> None:
    """Test seam: ``callable(url) -> str`` for feeds / parsed JSON for GDELT.
    ``None`` restores urllib."""
    global _HTTP
    _HTTP = fn
    invalidate_cache()


def invalidate_cache() -> None:
    with _LOCK:
        _SNAPSHOT_CACHE[0], _SNAPSHOT_CACHE[1] = 0.0, None


def _fetch_text(url: str) -> str:
    if _HTTP is not None:
        result = _HTTP(url)
        return result if isinstance(result, str) else ""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 (fixed https hosts)
        return resp.read(_MAX_RESP_BYTES + 1).decode("utf-8", errors="replace")


def _fetch_json(url: str):
    if _HTTP is not None:
        result = _HTTP(url)
        return result if isinstance(result, (dict, list)) else None
    import json
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
        return json.loads(resp.read(_MAX_RESP_BYTES + 1).decode("utf-8", errors="replace") or "{}")


# --------------------------------------------------------------------------- #
# RSS parsing (RSS 2.0; namespace-tolerant, no external deps)
# --------------------------------------------------------------------------- #
def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(xml_text: str, cap: int = _FEED_ITEM_CAP) -> list[dict]:
    try:
        root = ET.fromstring(xml_text.lstrip("﻿").strip())
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = _strip_html(item.findtext("title") or "")
        if not title:
            continue
        items.append({
            "title": title[:300],
            "link": (item.findtext("link") or "").strip()[:400],
            "published": (item.findtext("pubDate") or "").strip()[:64],
            "summary": _strip_html(item.findtext("description") or "")[:500],
        })
        if len(items) >= cap:
            break
    return items


def _lexicon_score(text: str, positive: dict, negative: dict) -> float:
    """Net intensity-weighted score in [-1, 1] for SHORT text (headlines):
    normalized by sqrt(length) so long headlines don't dominate."""
    tokens = _TOKEN.findall((text or "").lower())
    if not tokens:
        return 0.0
    score = sum(positive.get(t, 0.0) - negative.get(t, 0.0) for t in tokens)
    return float(max(-1.0, min(1.0, score / (len(tokens) ** 0.5) / 2.0)))


def _lexicon_balance(text: str, positive: dict, negative: dict) -> float:
    """Balance ratio in [-1, 1] for LONG documents: (pos - neg) / (pos + neg)
    over matched intensity, independent of document length — a 4,000-word FOMC
    minutes with 3:1 hawkish-to-dovish language reads +0.5 instead of being
    diluted toward zero by the sqrt normalization built for headlines."""
    tokens = _TOKEN.findall((text or "").lower())
    pos = sum(positive.get(t, 0.0) for t in tokens)
    neg = sum(negative.get(t, 0.0) for t in tokens)
    matched = pos + neg
    if matched < 3.0:  # too few signal terms to call a stance from
        return 0.0
    return float(max(-1.0, min(1.0, (pos - neg) / matched)))


# --------------------------------------------------------------------------- #
# Component builders (each guarded; failure -> available: False)
# --------------------------------------------------------------------------- #
_FED_FULLTEXT_BUDGET = 5   # newest documents whose full body is fetched+scored
_FED_FULLTEXT_WEIGHT = 3.0  # a scored statement/speech body outweighs a bare title


def _fed_component() -> dict:
    docs = []
    errors = []
    for label, url in (("press", FED_MONETARY_FEED), ("speech", FED_SPEECHES_FEED)):
        try:
            for item in _parse_rss(_fetch_text(url), cap=8):
                text = f"{item['title']}. {item['summary']}"
                docs.append({**item, "kind": label, "scored": "title",
                             "hawk_score": round(_lexicon_score(text, _HAWKISH, _DOVISH), 3)})
        except Exception as exc:
            errors.append(f"{label}: {exc}")
    if not docs:
        return {"available": False, "reason": "; ".join(errors) or "no items"}
    # Titles alone score near-zero ("Minutes of the FOMC ..." carries no
    # hawk/dove language). Fetch the FULL BODY of the newest documents —
    # bounded, cached at the snapshot level — and score the actual Fed speak.
    # Minutes press releases are stubs: the document lives one link deeper at
    # /monetarypolicy/fomcminutes*.htm (verified live: the stub matched 0.7
    # lexicon intensity; the real minutes matched 51 and read +0.51 hawkish).
    for doc in docs[:_FED_FULLTEXT_BUDGET]:
        link = doc.get("link") or ""
        if not link.startswith("https://www.federalreserve.gov"):
            continue
        try:
            raw_html = _fetch_text(link)
        except Exception:
            continue  # honest fallback: the title score stands, marked as such
        body = _strip_html(raw_html)[:60_000]
        deeper = re.search(r'href="(/monetarypolicy/fomc\w*\d+[a-z]?\.htm)"', raw_html)
        if deeper:
            try:
                deep_body = _strip_html(
                    _fetch_text("https://www.federalreserve.gov" + deeper.group(1)))[:60_000]
                if len(deep_body) > len(body):
                    body = deep_body
            except Exception:
                pass  # the stub body is still better than the bare title
        if len(body) > 400:  # a real document, not an error/redirect stub
            doc["hawk_score"] = round(_lexicon_balance(body, _HAWKISH, _DOVISH), 3)
            doc["scored"] = "full_text"
    weights = [(_FED_FULLTEXT_WEIGHT if d["scored"] == "full_text" else 1.0) for d in docs]
    stance = sum(w * d["hawk_score"] for w, d in zip(weights, docs)) / sum(weights)
    label = ("hawkish" if stance > 0.08 else "dovish" if stance < -0.08 else "neutral")
    return {
        "available": True,
        "stance_score": round(stance, 3),        # -1 dovish .. +1 hawkish
        "stance_label": label,
        "n_documents": len(docs),
        "n_full_text": sum(1 for d in docs if d["scored"] == "full_text"),
        "documents": docs[:10],
        "method": ("intensity-weighted hawk/dove lexicon; newest documents scored on the "
                   "FULL official text (weighted 3x), the rest on title+summary"),
    }


def _policy_component() -> dict:
    try:
        items = _parse_rss(_fetch_text(WH_ACTIONS_FEED), cap=_FEED_ITEM_CAP)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    if not items:
        return {"available": False, "reason": "no items"}
    theme_counts: dict[str, int] = {}
    sector_pressure: dict[str, int] = {}
    tagged = []
    for item in items:
        tokens = set(_TOKEN.findall(f"{item['title']} {item['summary']}".lower()))
        themes = sorted({_POLICY_THEMES[t][0] for t in tokens if t in _POLICY_THEMES})
        sectors = sorted({s for t in tokens if t in _POLICY_THEMES for s in _POLICY_THEMES[t][1]})
        for theme in themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        for sector in sectors:
            sector_pressure[sector] = sector_pressure.get(sector, 0) + 1
        tagged.append({**item, "themes": themes, "sectors": sectors})
    return {
        "available": True,
        "n_actions": len(tagged),
        "themes": dict(sorted(theme_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "sector_pressure": dict(sorted(sector_pressure.items(), key=lambda kv: kv[1], reverse=True)),
        "actions": tagged,
        "method": "keyword theme-tagging of official White House presidential actions",
    }


def _geopolitics_component() -> dict:
    url = GDELT_DOC_URL + "?" + urllib.parse.urlencode({
        "query": GDELT_GEOPOLITICS_QUERY,
        "mode": "artlist", "format": "json",
        "maxrecords": str(_GDELT_MAX_RECORDS), "timespan": "3d",
    })
    try:
        payload = _fetch_json(url) or {}
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    articles = payload.get("articles") or []
    if not articles:
        return {"available": False, "reason": "no articles returned (possibly rate-limited)"}
    seen: set[str] = set()
    scored = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        title = re.sub(r"\s+", " ", str(art.get("title") or "")).strip()
        key = title.lower()
        if not title or key in seen:
            continue
        seen.add(key)
        scored.append({"title": title[:300],
                       "risk_score": round(_lexicon_score(title, _GEO_RISK, _GEO_CALM), 3)})
    if not scored:
        return {"available": False, "reason": "no parseable articles"}
    # Index: mean headline risk mapped to [0, 1]; matched-volume saturation term
    # so ten hot headlines rank above one.
    mean_risk = sum(max(0.0, s["risk_score"]) for s in scored) / len(scored)
    volume = min(1.0, len(scored) / 25.0)
    index = round(min(1.0, 0.7 * min(1.0, mean_risk * 2.5) + 0.3 * volume), 3)
    level = "elevated" if index >= 0.6 else "moderate" if index >= 0.35 else "calm"
    return {
        "available": True,
        "risk_index": index,          # 0 calm .. 1 severe
        "risk_level": level,
        "n_articles": len(scored),
        "headlines": sorted(scored, key=lambda s: s["risk_score"], reverse=True)[:8],
        "method": "conflict/escalation lexicon over a fixed GDELT world-news query (3d window)",
    }


def next_fomc(today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    for start_s, end_s in FOMC_MEETINGS_2026:
        end = date.fromisoformat(end_s)
        if end >= today:
            days_until = (date.fromisoformat(start_s) - today).days
            return {
                "start": start_s,
                "end": end_s,
                "days_until": max(days_until, 0) if days_until >= 0 else 0,
                "in_progress": date.fromisoformat(start_s) <= today <= end,
                "imminent": days_until <= FOMC_IMMINENT_DAYS,
                "source": "published FOMC schedule (static; verify after each calendar year)",
            }
    return {"start": "", "end": "", "days_until": None, "in_progress": False,
            "imminent": False, "source": "schedule exhausted — update FOMC_MEETINGS list"}


def rate_odds() -> dict:
    """Market-implied odds of a policy move at the next FOMC meeting.

    Read from CME 30-day Fed funds futures (the same day-weighted arithmetic
    behind CME FedWatch): the meeting-month contract prices the average of
    pre- and post-meeting rates; the following month (no meeting) prices the
    post-meeting rate outright. This is OBSERVATION of what the market has
    already priced — never a Helios prediction. Offline -> available False.
    """
    from . import macro as _macro
    meeting = next_fomc()
    start = meeting.get("start") or ""
    if len(start) != 10:
        return {"available": False, "reason": "No upcoming FOMC meeting on the calendar."}
    m_year, m_month, m_day = int(start[:4]), int(start[5:7]), int(start[8:10])
    # Rate change takes effect the day after the meeting ends (start day + 1
    # for a two-day meeting is close enough for monthly averaging).
    effective_day = min(m_day + 2, calendar.monthrange(m_year, m_month)[1])
    next_month = m_month + 1 if m_month < 12 else 1
    next_year = m_year if m_month < 12 else m_year + 1
    meeting_avg = _macro.fed_funds_implied(m_year, m_month)
    post_rate = _macro.fed_funds_implied(next_year, next_month)
    if meeting_avg is None or post_rate is None:
        return {"available": False,
                "reason": "Fed funds futures unavailable (offline or contract not served)."}
    days_in_month = calendar.monthrange(m_year, m_month)[1]
    pre_days = effective_day - 1
    post_days = days_in_month - pre_days
    if pre_days <= 0 or post_days <= 0:
        return {"available": False, "reason": "Meeting timing degenerate for day-weighting."}
    # meeting_avg = (pre_days/N)*pre_rate + (post_days/N)*post_rate  ->  pre_rate:
    pre_rate = (meeting_avg - (post_days / days_in_month) * post_rate) / (pre_days / days_in_month)
    change_bps = (post_rate - pre_rate) * 10_000
    # Two-outcome FedWatch simplification: hold vs the nearest 25bp move.
    prob_move = max(0.0, min(1.0, abs(change_bps) / 25.0))
    direction = "hike" if change_bps > 0 else "cut" if change_bps < 0 else "hold"
    return {
        "available": True,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meeting": start,
        "days_until": meeting.get("days_until"),
        "implied_rate_before_pct": round(pre_rate * 100, 3),
        "implied_rate_after_pct": round(post_rate * 100, 3),
        "implied_change_bps": round(change_bps, 1),
        "prob_25bp_move_pct": round(prob_move * 100, 1),
        "direction": direction,
        "strip": _macro.fed_funds_strip(6),
        "source": "cme_fed_funds_futures_via_yfinance",
        "basis": ("Day-weighted decomposition of the meeting-month contract vs the "
                  "following month (CME FedWatch arithmetic, two-outcome hold-vs-25bp "
                  "simplification). Market-implied pricing, not a Helios forecast."),
    }


# --------------------------------------------------------------------------- #
# Snapshot (cached) + the transparent event-risk summary consumed by signals
# --------------------------------------------------------------------------- #
def macro_snapshot(force: bool = False) -> dict:
    """Full macro picture, cached ~30 minutes. Never raises."""
    with _LOCK:
        ts, cached = _SNAPSHOT_CACHE
        if cached is not None and not force and time.monotonic() - ts < _SNAPSHOT_TTL_S:
            return dict(cached)
    # Single-flight the cold build: it performs up to ~14 network fetches, and
    # without this every concurrent analyze arriving on an expired cache
    # rebuilt it in parallel — a redundant fetch herd (review finding).
    # _FETCH_LOCK is separate from _LOCK so cache reads never queue behind a
    # slow build; the double-check inside returns the copy a winner just made.
    with _FETCH_LOCK:
        with _LOCK:
            ts, cached = _SNAPSHOT_CACHE
            if cached is not None and not force and time.monotonic() - ts < _SNAPSHOT_TTL_S:
                return dict(cached)
        fed = _fed_component()
        policy = _policy_component()
        geo = _geopolitics_component()
        fomc = next_fomc()
        try:
            odds = rate_odds()
        except Exception:
            odds = {"available": False, "reason": "rate-odds computation failed"}
    snapshot = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fed": fed,
        "policy": policy,
        "geopolitics": geo,
        "fomc": fomc,
        "rate_odds": odds,
        "event_risk": _event_risk(fed, geo, fomc),
        "disclaimer": ("Deterministic lexicon scores over primary-source feeds — context and "
                       "conviction dampers, not return forecasts. Sources that failed to load "
                       "are marked unavailable, never assumed calm."),
    }
    with _LOCK:
        _SNAPSHOT_CACHE[0], _SNAPSHOT_CACHE[1] = time.monotonic(), snapshot
    _persist_reading(snapshot)
    return dict(snapshot)


def _persist_reading(snapshot: dict) -> None:
    """One reading per UTC day (upsert) — enables stance/GPR CHANGE signals.
    Lazy import avoids an engine import cycle; failure never blocks a snapshot."""
    try:
        from . import persistence
        fed = snapshot.get("fed") or {}
        geo = snapshot.get("geopolitics") or {}
        policy = snapshot.get("policy") or {}
        fomc = snapshot.get("fomc") or {}
        persistence.get_store().record_macro_reading({
            "reading_date": datetime.now(timezone.utc).date().isoformat(),
            "fed_stance": fed.get("stance_score") if fed.get("available") else None,
            "fed_n_documents": fed.get("n_documents") if fed.get("available") else None,
            "gpr_index": geo.get("risk_index") if geo.get("available") else None,
            "fomc_days_until": fomc.get("days_until"),
            "policy_themes": policy.get("themes") or {},
        })
    except Exception:
        pass


def history_and_changes(limit: int = 30) -> dict:
    """Recent daily readings plus ~7-day deltas. A change is only reported when
    BOTH endpoints exist — no delta is fabricated across unavailable days."""
    try:
        from . import persistence
        rows = persistence.get_store().macro_history(limit=limit)
    except Exception:
        rows = []
    out: dict = {"readings": rows, "fed_stance_change_7d": None, "gpr_change_7d": None}
    if len(rows) >= 2:
        latest = rows[0]
        past = next((r for r in rows[1:]
                     if r["reading_date"] <= _days_ago_iso(7)), rows[-1])
        if latest.get("fed_stance") is not None and past.get("fed_stance") is not None:
            out["fed_stance_change_7d"] = round(latest["fed_stance"] - past["fed_stance"], 3)
        if latest.get("gpr_index") is not None and past.get("gpr_index") is not None:
            out["gpr_change_7d"] = round(latest["gpr_index"] - past["gpr_index"], 3)
        out["compared_to"] = past.get("reading_date")
    return out


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()


def snapshot_cached() -> dict | None:
    """Get-only cache read for latency-sensitive surfaces (the radar)."""
    with _LOCK:
        ts, cached = _SNAPSHOT_CACHE
    if cached is not None and time.monotonic() - ts < _SNAPSHOT_TTL_S:
        return dict(cached)
    return None


def _event_risk(fed: dict, geo: dict, fomc: dict) -> dict:
    """The compact, transparent block signals.evaluate consumes."""
    gpr = float(geo.get("risk_index") or 0.0) if geo.get("available") else None
    return {
        "fomc_imminent": bool(fomc.get("imminent")),
        "fomc_days_until": fomc.get("days_until"),
        "gpr_index": gpr,
        "gpr_elevated": bool(gpr is not None and gpr >= 0.6),
        "fed_stance_score": fed.get("stance_score") if fed.get("available") else None,
        "fed_stance_label": fed.get("stance_label") if fed.get("available") else None,
    }


def compact_summary(snapshot: dict | None) -> dict | None:
    """Small macro block for dashboards/payloads (full detail via /api/macro)."""
    if not snapshot:
        return None
    fed = snapshot.get("fed") or {}
    geo = snapshot.get("geopolitics") or {}
    policy = snapshot.get("policy") or {}
    fomc = snapshot.get("fomc") or {}
    odds = snapshot.get("rate_odds") or {}
    return {
        "as_of": snapshot.get("as_of"),
        "rate_odds": ({
            "meeting": odds.get("meeting"),
            "direction": odds.get("direction"),
            "prob_25bp_move_pct": odds.get("prob_25bp_move_pct"),
            "implied_change_bps": odds.get("implied_change_bps"),
            "basis": "market-implied (Fed funds futures), not a forecast",
        } if odds.get("available") else {"available": False}),
        "fed_available": bool(fed.get("available")),
        "fed_stance_label": fed.get("stance_label"),
        "fed_stance_score": fed.get("stance_score"),
        "gpr_available": bool(geo.get("available")),
        "gpr_index": geo.get("risk_index"),
        "gpr_level": geo.get("risk_level"),
        "policy_available": bool(policy.get("available")),
        "policy_themes": dict(list((policy.get("themes") or {}).items())[:5]),
        "fomc_start": fomc.get("start"),
        "fomc_days_until": fomc.get("days_until"),
        "fomc_imminent": bool(fomc.get("imminent")),
    }


def build_macro_context(sector: str = "", snapshot: dict | None = None) -> dict | None:
    """The macro_context dict signals.evaluate consumes, or None when no
    snapshot exists (no data is never treated as risk or calm)."""
    snap = snapshot if snapshot is not None else snapshot_cached()
    if not snap:
        return None
    ctx = dict(snap.get("event_risk") or {})
    pressure = sector_policy_pressure(sector, snap)
    if pressure:
        ctx["sector_policy"] = pressure
    # Earnings-season breadth (cached-only read — this path never fetches):
    # broadly deteriorating reported results are event risk the same way FOMC
    # proximity is. Absent/insufficient data adds NOTHING (never fake calm).
    try:
        from . import earnings_breadth as _eb
        breadth = _eb.snapshot_cached()
        if breadth and breadth.get("sufficient"):
            ctx["earnings_breadth_pct"] = breadth.get("breadth_pct")
            ctx["earnings_breadth_deteriorating"] = bool(breadth.get("deteriorating"))
            ctx["earnings_reports"] = breadth.get("reports")
    except Exception:
        pass
    return ctx


def sector_policy_pressure(sector: str, snapshot: dict | None = None) -> dict | None:
    """Active White House policy pressure on one sector, or None."""
    snap = snapshot or snapshot_cached()
    if not snap:
        return None
    policy = snap.get("policy") or {}
    if not policy.get("available"):
        return None
    key = (sector or "").strip().lower()
    if not key:
        return None
    for name, count in (policy.get("sector_pressure") or {}).items():
        if key in name or name in key:
            themes = [t for t, _ in (policy.get("themes") or {}).items()]
            return {"sector": name, "n_actions": count, "themes": themes[:4]}
    return None

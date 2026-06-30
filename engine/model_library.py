"""Governed starter model library.

These templates are analysis-only workspaces. They provide real public tickers,
mandates, benchmarks, rebalance rules, risk limits, and provenance metadata, but
they do not bypass Helios real-data gates or become investment advice.
"""
from __future__ import annotations

from copy import deepcopy

from . import portfolio

_CAVEAT = (
    "Curated starter template for research workflow setup; not investment "
    "advice, not a managed account model, and not a guarantee of return."
)
_VERSION = "2026-06-30"


_TEMPLATES = (
    {
        "slug": "ai-infrastructure",
        "model_id": "AI-INFRASTRUCTURE",
        "name": "AI Infrastructure Compounders",
        "category": "AI infrastructure",
        "mandate": "pure_growth",
        "benchmark": "QQQ",
        "rebalance_rules": {"frequency": "quarterly", "drift_band_pct": 5.0, "review_trigger": "review if any holding breaches risk limits or loses live-data coverage"},
        "risk_limits": {"max_single_position_pct": 20.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 10.0, "min_holdings": 8},
        "holdings": (("NVDA", 18), ("MSFT", 14), ("AVGO", 12), ("AMZN", 10), ("GOOGL", 10), ("AMD", 8), ("META", 8), ("TSM", 8), ("SMH", 7), ("VRT", 5)),
        "thesis": "Semiconductors, hyperscale cloud, and power/thermal infrastructure exposed to AI capital spending.",
    },
    {
        "slug": "quality-compounders",
        "model_id": "QUALITY-COMPOUNDERS",
        "name": "Quality Compounders and Resilience",
        "category": "quality compounders",
        "mandate": "balanced",
        "benchmark": "SPY",
        "rebalance_rules": {"frequency": "quarterly", "drift_band_pct": 4.0, "review_trigger": "review if quality sleeve drops below 60% or defensive sleeve exceeds 25%"},
        "risk_limits": {"max_single_position_pct": 15.0, "max_theme_position_pct": 80.0, "max_etf_position_pct": 18.0, "min_holdings": 9},
        "holdings": (("MSFT", 12), ("AAPL", 10), ("GOOGL", 10), ("META", 8), ("JPM", 8), ("LLY", 8), ("QUAL", 14), ("VIG", 12), ("USMV", 10), ("SGOV", 8)),
        "thesis": "Durable large-cap earnings quality with low-volatility and cash ballast.",
    },
    {
        "slug": "defense-security",
        "model_id": "DEFENSE-SECURITY",
        "name": "Defense and Cyber Security",
        "category": "defense/security",
        "mandate": "balanced",
        "benchmark": "ITA",
        "rebalance_rules": {"frequency": "semiannual", "drift_band_pct": 5.0, "review_trigger": "review after major budget, conflict, or cybersecurity regulatory changes"},
        "risk_limits": {"max_single_position_pct": 15.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 18.0, "min_holdings": 8},
        "holdings": (("ITA", 15), ("XAR", 10), ("LMT", 10), ("RTX", 10), ("NOC", 8), ("GD", 7), ("CIBR", 12), ("PANW", 10), ("CRWD", 10), ("FTNT", 8)),
        "thesis": "Aerospace, defense primes, and cybersecurity providers tied to security spending.",
    },
    {
        "slug": "energy-grid",
        "model_id": "ENERGY-GRID",
        "name": "Energy and Grid Infrastructure",
        "category": "energy/grid",
        "mandate": "balanced",
        "benchmark": "XLU",
        "rebalance_rules": {"frequency": "quarterly", "drift_band_pct": 5.0, "review_trigger": "review if utilities, energy, or infrastructure sleeve exceeds stated cap"},
        "risk_limits": {"max_single_position_pct": 15.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 15.0, "min_holdings": 8},
        "holdings": (("VST", 12), ("CEG", 12), ("XLU", 12), ("ETN", 12), ("PAVE", 12), ("NEE", 10), ("GRID", 10), ("GE", 8), ("XOM", 7), ("GLD", 5)),
        "thesis": "Power generation, grid equipment, industrial infrastructure, and real-asset ballast.",
    },
    {
        "slug": "healthcare-innovation",
        "model_id": "HEALTHCARE-INNOVATION",
        "name": "Healthcare Innovation and Longevity",
        "category": "healthcare innovation",
        "mandate": "balanced",
        "benchmark": "XLV",
        "rebalance_rules": {"frequency": "quarterly", "drift_band_pct": 4.0, "review_trigger": "review after trial, reimbursement, patent, or regulatory events"},
        "risk_limits": {"max_single_position_pct": 18.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 20.0, "min_holdings": 8},
        "holdings": (("XLV", 18), ("VHT", 12), ("IHI", 12), ("LLY", 12), ("IBB", 10), ("UNH", 10), ("ISRG", 8), ("TMO", 7), ("SYK", 6), ("ABBV", 5)),
        "thesis": "Healthcare services, medtech, biotech, and longevity-related large-cap innovation.",
    },
    {
        "slug": "inflation-hedges",
        "model_id": "INFLATION-HEDGES",
        "name": "Inflation and Fragmentation Hedges",
        "category": "inflation hedges",
        "mandate": "income",
        "benchmark": "TIP",
        "rebalance_rules": {"frequency": "quarterly", "drift_band_pct": 5.0, "review_trigger": "review if real-rate, energy, or gold sleeve exceeds risk budget"},
        "risk_limits": {"max_single_position_pct": 15.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 15.0, "min_holdings": 8},
        "holdings": (("PAVE", 14), ("XLE", 12), ("GLD", 12), ("TIP", 12), ("XOM", 10), ("XLU", 10), ("CEG", 8), ("BND", 8), ("VST", 7), ("SGOV", 7)),
        "thesis": "Infrastructure, energy, gold, TIPS, and cash ballast for inflation/geopolitical stress.",
    },
    {
        "slug": "cash-defensive",
        "model_id": "CASH-DEFENSIVE",
        "name": "Cash and Defensive Reserve",
        "category": "cash/defensive",
        "mandate": "cd_alternative",
        "benchmark": "SGOV",
        "rebalance_rules": {"frequency": "monthly", "drift_band_pct": 2.0, "review_trigger": "review if drawdown or duration exposure exceeds cash-alternative budget"},
        "risk_limits": {"max_single_position_pct": 35.0, "max_theme_position_pct": 100.0, "max_etf_position_pct": 35.0, "min_holdings": 6},
        "holdings": (("SGOV", 35), ("BND", 20), ("TLT", 10), ("USMV", 10), ("VIG", 8), ("XLU", 7), ("XLV", 5), ("GLD", 5)),
        "thesis": "Near-cash Treasury exposure with defensive equity, bond, utility, healthcare, and gold ballast.",
    },
)


def public_list() -> list[dict]:
    return [_public_template(template) for template in _TEMPLATES]


def get(slug: str) -> dict | None:
    key = (slug or "").strip().lower()
    for template in _TEMPLATES:
        if template["slug"] == key:
            return _public_template(template)
    return None


def import_template(slug: str) -> portfolio.Model | None:
    template = get(slug)
    if not template:
        return None
    model = portfolio.Model(
        id=template["model_id"],
        name=template["name"],
        mandate_key=template["mandate"],
        mandate_context=_context(template),
        holdings=[portfolio.Holding(holding["ticker"], float(holding["weight"])) for holding in template["holdings"]],
    )
    portfolio.register(model)
    portfolio._persist_model(model, source_filename=f"model-library-{template['slug']}.csv")
    return model


def _public_template(template: dict) -> dict:
    out = deepcopy(template)
    total = sum(float(weight) for _, weight in out["holdings"]) or 1.0
    out["holdings"] = [{"ticker": ticker, "weight": round(float(weight) / total, 6)} for ticker, weight in out["holdings"]]
    out["template_only"] = True
    out["provenance"] = {
        "source_type": "curated_template",
        "version": _VERSION,
        "basis": "real public tickers curated for Helios model-governance workflow setup",
        "caveat": _CAVEAT,
    }
    return out


def _context(template: dict) -> str:
    limits = template["risk_limits"]
    rules = template["rebalance_rules"]
    return (
        "Governed starter template; not investment advice. "
        f"Benchmark: {template['benchmark']}. "
        f"Rebalance: {rules['frequency']}, {rules['drift_band_pct']:.1f}% drift band. "
        f"Risk limits: max single {limits['max_single_position_pct']:.1f}%, min holdings {limits['min_holdings']}. "
        f"Provenance: curated template v{template['provenance']['version']}; requires live/uploaded history for real research."
    )

"""Versioned, operator-authored research context for instrument evidence.

Model context already lives in the governed model record. Instrument context is
stored separately so Strategy Lab can judge a ticker against an explicit thesis
without changing instrument identity or inferring an investment mandate.
"""
from __future__ import annotations

import re
from typing import Any

from . import data, evidence, mandate, persistence, signal_journal

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def instrument_context(symbol: str) -> dict[str, Any] | None:
    events = persistence.get_store().research_context_events(
        "instrument", data.clean_symbol(symbol, fallback=""), limit=1,
    )
    return _public_event(events[0]) if events else None


def model_context(model) -> dict[str, Any]:
    thesis = _clean_text(getattr(model, "thesis", ""), 2_000)
    mandate_key = str(getattr(model, "mandate_key", "") or "")
    mandate_details = mandate.get(mandate_key)
    return {
        "configured": bool(thesis),
        "target_kind": "model",
        "target_id": str(getattr(model, "id", "")),
        "target_name": str(getattr(model, "name", "")),
        "thesis": thesis,
        "thesis_params": dict(getattr(model, "thesis_params", {}) or {}),
        "mandate_key": mandate_key,
        "mandate_label": mandate_details["label"],
        "benchmark": signal_journal.benchmark_for_model(model),
        "horizon_days": None,
        "invalidation_criteria": [],
        "governance_source": "model_version",
    }


def save_instrument_context(
    inst: data.Instrument,
    *,
    thesis: str,
    mandate_key: str,
    benchmark: str,
    horizon_days: int,
    invalidation_criteria: list[str],
    change_note: str,
    actor: str,
) -> dict[str, Any]:
    store = persistence.get_store()
    if not store.available:
        raise RuntimeError("Local persistence is required to save governed research context.")

    clean_thesis = _clean_text(thesis, 2_000)
    if len(clean_thesis) < 20:
        raise ValueError("Thesis must contain at least 20 characters.")
    clean_mandate = str(mandate_key or "").strip().lower()
    if clean_mandate not in mandate.MANDATES:
        raise ValueError("Select a recognized mandate before saving research context.")
    clean_benchmark = data.clean_symbol(benchmark, fallback="")
    if not clean_benchmark:
        raise ValueError("Benchmark is required.")
    try:
        clean_horizon = int(horizon_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("Horizon must be an integer number of sessions.") from exc
    if not 5 <= clean_horizon <= 252:
        raise ValueError("Horizon must be between 5 and 252 sessions.")

    criteria = [
        _clean_text(item, 300)
        for item in (invalidation_criteria if isinstance(invalidation_criteria, list) else [])
    ]
    criteria = [item for item in criteria if item]
    if not 1 <= len(criteria) <= 8 or any(len(item) < 5 for item in criteria):
        raise ValueError("Provide 1 to 8 invalidation criteria of at least 5 characters each.")
    clean_note = _clean_text(change_note, 800)
    if len(clean_note) < 5:
        raise ValueError("A change note of at least 5 characters is required.")
    clean_actor = _clean_text(actor, 120) or "Advisor Console"

    version = store.next_research_context_version("instrument", inst.symbol)
    evidence_id = evidence.new_id("researchctx")
    evidence_ref = evidence.reference(evidence_id)
    snapshot = {
        "target_kind": "instrument",
        "target_id": inst.symbol,
        "target_name": inst.name,
        "version": version,
        "actor": clean_actor,
        "thesis": clean_thesis,
        "mandate_key": clean_mandate,
        "mandate_label": mandate.MANDATES[clean_mandate]["label"],
        "benchmark": clean_benchmark,
        "horizon_days": clean_horizon,
        "invalidation_criteria": criteria,
        "change_note": clean_note,
        "evidence": evidence_ref,
    }
    with store.transaction(durable=True):
        event = store.record_research_context_event(
            target_kind="instrument",
            target_id=inst.symbol,
            target_name=inst.name,
            version=version,
            actor=clean_actor,
            thesis=clean_thesis,
            mandate_key=clean_mandate,
            benchmark=clean_benchmark,
            horizon_days=clean_horizon,
            invalidation_criteria=criteria,
            change_note=clean_note,
            evidence_ref=evidence_ref,
        )
        if event is None:
            raise RuntimeError(store.warning or "Research context could not be persisted.")
        captured = evidence.capture(
            evidence_id=evidence_id,
            artifact_kind="research_context",
            target_kind="instrument",
            target_id=inst.symbol,
            input_payload={
                "target_name": inst.name,
                "operator_context": snapshot,
            },
            output_payload=event,
            evidence_manifest=evidence.manifest(
                source=inst.source,
                provider=getattr(inst, "price_provider", ""),
                retrieved_at=getattr(inst, "retrieved_at", ""),
                model_version=version,
                transformations=("operator_validation", "versioned_context_capture"),
                extra={"governed_context": True},
            ),
        )
        if not captured.get("recorded"):
            raise RuntimeError("Research-context evidence could not be persisted.")
    return _public_event(event)


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    key = str(event.get("mandate_key") or "")
    return {
        **event,
        "configured": bool(event.get("thesis")),
        "mandate_label": mandate.get(key)["label"] if key in mandate.MANDATES else "",
        "governance_source": "instrument_context_history",
    }


def _clean_text(value: Any, limit: int) -> str:
    return _CONTROL_RE.sub("", str(value or "").strip())[:limit]

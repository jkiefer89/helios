"""Regression tests for the reports-exports audit fixes.

Covers: numeric zeros in HTML exports, honest exec-summary score labels on
real model PDFs, concise evidence pages without legal boilerplate, and the
strict include-AI-narrative toggle resolver.
"""
from io import BytesIO

import app as helios
from conftest import price_csv
from engine import ai_copilot, report_exports, report_snapshots


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _page_count(pdf: bytes) -> int:
    # "/Type /Pages" (the page tree node) also matches "/Type /Page".
    return pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages")


def _upload_real_model(client, symbols: tuple[str, str], model_name: str) -> str:
    for symbol in symbols:
        upload = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(price_csv(days=260)), f"{symbol}.csv"),
                "symbol": symbol,
                "name": f"{symbol} uploaded history",
            },
            content_type="multipart/form-data",
        )
        assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(f"Ticker,Weight\n{symbols[0]},60\n{symbols[1]},40\n".encode("utf-8")), "fix-model.csv"),
            "name": model_name,
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    return model_upload.get_json()["id"]


# --- FIX 1: numeric zeros must render in HTML exports ---


def test_esc_blanks_only_none_and_keeps_zeros():
    assert report_exports._esc(0) == "0"
    assert report_exports._esc(0.0) == "0.0"
    assert report_exports._esc(None) == ""
    assert report_exports._esc("") == ""


def test_zero_section_values_render_in_html_export():
    report = {
        "title": "Zero Render Check",
        "kind": "instrument",
        "data_mode": "real",
        "eligible_for_real_research": True,
        "data_provenance": {"source": "upload", "row_count": 260, "data_mode": "real"},
        "sections": {"evidence": {"opportunity_score": 0, "risk_score": 0.0}},
        "warnings": [],
    }
    snapshot = report_exports.build_snapshot(target_kind="instrument", target_id="ZERO", report=report)

    html_text = snapshot["html"]
    assert "<dt>Opportunity Score</dt><dd>0</dd>" in html_text
    assert "<dt>Risk Score</dt><dd>0.0</dd>" in html_text


def test_zero_risk_cell_renders_in_html_table():
    assert report_exports._format_risk_cell(0) == "0"
    assert report_exports._format_risk_cell(None) == ""


def test_saved_ai_narrative_ignores_legacy_compliance_field():
    narrative = report_exports.ai_result_to_narrative({
        "summary": "Evidence summary.",
        "data_quality_statement": "Current live history is eligible.",
        "compliance_caveats": ["Legacy legal boilerplate."],
    })

    assert "Evidence summary." in narrative
    assert "Current live history is eligible." in narrative
    assert "Legacy legal boilerplate" not in narrative


# --- FIX 2: real-model PDFs must not claim 'Research locked' for unscored fields ---


def test_real_model_pdf_uses_not_scored_label_instead_of_research_locked():
    client = _client()
    model_id = _upload_real_model(client, ("RXA", "RXB"), "Real Export Model")
    report = client.get(f"/api/report/model?id={model_id}").get_json()
    assert report["eligible_for_real_research"] is True
    assert report["data_mode"] == "real"
    # Model reports never carry these instrument-scale scores.
    assert "opportunity_score" not in report["sections"]["evidence"]

    snapshot = report_exports.build_snapshot(target_kind="model", target_id=model_id, report=report)
    pdf = report_exports.render_pdf(snapshot)

    assert pdf.startswith(b"%PDF-")
    assert b"Not scored for models" in pdf
    assert b"Research locked" not in pdf


def test_blocked_model_pdf_still_shows_research_locked(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nFAKEXA,50\nFAKEXB,50\n"), "sim-fix.csv"),
            "name": "Sim Fix Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]
    report = client.get(f"/api/report/model?id={model_id}").get_json()
    assert report["eligible_for_real_research"] is False

    snapshot = report_exports.build_snapshot(target_kind="model", target_id=model_id, report=report)
    pdf = report_exports.render_pdf(snapshot)

    # Provenance gate stays intact: blocked data modes keep the locked label.
    assert b"Research locked" in pdf
    assert b"Not scored for models" not in pdf


def test_missing_score_label_reads_report_provenance():
    real_model = {"kind": "model", "eligible_for_real_research": True, "data_provenance": {"data_mode": "real"}}
    blocked_model = {"kind": "model", "eligible_for_real_research": False, "data_provenance": {"data_mode": "invalid_for_research"}}
    demo_instrument = {"kind": "instrument", "eligible_for_real_research": False, "data_provenance": {"data_mode": "demo"}}
    assert report_exports._missing_score_label(real_model) == "Not scored for models"
    assert report_exports._missing_score_label(blocked_model) == "Research locked"
    assert report_exports._missing_score_label(demo_instrument) == "Research locked"


# --- FIX 3: internal reports keep evidence warnings without legal boilerplate ---


def _maximal_snapshot() -> dict:
    long_warning = (
        "This caveat is intentionally verbose so that it wraps across two full lines "
        "of the evidence page layout when the institutional PDF package is rendered, "
        "which exercises the maximal-content pagination path end to end."
    )
    narrative_line = "The AI narrative line repeats to fill all eight wrapped lines of the evidence page. "
    report = {
        "title": "Overflow Check",
        "kind": "instrument",
        "data_mode": "real",
        "eligible_for_real_research": True,
        "data_provenance": {"data_mode": "real"},
        "sections": {},
        "warnings": [f"{long_warning} #{index}" for index in range(5)],
    }
    return report_exports.build_snapshot(
        target_kind="instrument",
        target_id="OVERFLOW",
        report=report,
        ai_narrative=narrative_line * 20,
    )


def test_pdf_evidence_page_keeps_warnings_and_omits_legal_blocks():
    snapshot = _maximal_snapshot()
    assert len(snapshot["warnings"]) == 5
    assert snapshot["ai_narrative"]
    assert "disclosure_blocks" not in snapshot

    pdf = report_exports.render_pdf(snapshot)
    assert pdf.startswith(b"%PDF-")
    assert _page_count(pdf) == 6
    assert b"DATA AND MODEL WARNINGS" in pdf
    assert b"AI NARRATIVE" in pdf
    assert b"DISCLOSURE BLOCKS" not in pdf


def test_pdf_without_warnings_keeps_six_pages():
    report = {"title": "Baseline", "kind": "instrument", "sections": {}, "warnings": []}
    snapshot = report_exports.build_snapshot(target_kind="instrument", target_id="BASE", report=report)

    pdf = report_exports.render_pdf(snapshot)
    assert _page_count(pdf) == 6
    assert b"No additional data or model-quality warnings" in pdf
    assert b"DISCLOSURE BLOCKS" not in pdf


# --- FIX 4: include-AI-narrative toggle must be honored by the strict resolver ---


def _forbid_provider(monkeypatch):
    def _fail():  # pragma: no cover - only reached on regression
        raise AssertionError("provider must not be consulted")

    monkeypatch.setattr(ai_copilot, "get_provider", _fail)


def test_resolve_narrative_toggle_on_keeps_provided_narrative(monkeypatch):
    _forbid_provider(monkeypatch)
    narrative, meta = report_snapshots.resolve_narrative(
        {"kind": "instrument"},
        provided_narrative="Session narrative for advisor review.",
        include_ai_narrative=True,
    )
    assert narrative == "Session narrative for advisor review."
    assert meta["status"] == "provided"
    assert meta["provider"]["needs_review"] is True


def test_resolve_narrative_toggle_off_excludes_provided_narrative(monkeypatch):
    _forbid_provider(monkeypatch)
    narrative, meta = report_snapshots.resolve_narrative(
        {"kind": "instrument"},
        provided_narrative="Session narrative that the advisor toggled off.",
        include_ai_narrative=False,
    )
    assert narrative == ""
    assert meta["status"] == "not_requested"
    assert meta["provider"] == {}


def test_resolve_narrative_unspecified_toggle_keeps_legacy_provided_behavior(monkeypatch):
    _forbid_provider(monkeypatch)
    narrative, meta = report_snapshots.resolve_narrative(
        {"kind": "instrument"},
        provided_narrative="Legacy caller narrative.",
        include_ai_narrative=None,
    )
    assert narrative == "Legacy caller narrative."
    assert meta["status"] == "provided"


def test_resolve_narrative_toggle_off_snapshot_excludes_narrative(monkeypatch):
    _forbid_provider(monkeypatch)
    report = {"title": "Toggle Check", "kind": "instrument", "sections": {}, "warnings": []}
    narrative, meta = report_snapshots.resolve_narrative(
        report,
        provided_narrative="Narrative generated earlier in-session.",
        include_ai_narrative=False,
    )
    snapshot = report_exports.build_snapshot(
        target_kind="instrument",
        target_id="TOGGLE",
        report=report,
        ai_narrative=narrative,
        ai_narrative_status=meta["status"],
        ai_provider=meta["provider"],
    )
    assert snapshot["ai_narrative"] == ""
    assert snapshot["ai_narrative_included"] is False
    assert snapshot["ai_narrative_status"] == "not_requested"
    assert "Narrative generated earlier in-session." not in snapshot["html"]
    assert "AI Narrative" not in snapshot["html"]


def test_legacy_ai_narrative_shim_behavior_is_unchanged(monkeypatch):
    # The /api/report/snapshots route collapses an absent toggle to False, so
    # the legacy entry point must keep including a provided narrative until
    # helios_web/reports.py forwards the toggle as tri-state (see blockers).
    _forbid_provider(monkeypatch)
    narrative, meta = report_snapshots.ai_narrative(
        {"kind": "instrument"},
        provided_narrative="Legacy route narrative.",
        include_ai_narrative=False,
    )
    assert narrative == "Legacy route narrative."
    assert meta["status"] == "provided"

    narrative, meta = report_snapshots.ai_narrative(
        {"kind": "instrument"},
        provided_narrative="",
        include_ai_narrative=False,
    )
    assert narrative == ""
    assert meta["status"] == "not_requested"


def test_resolve_narrative_generates_when_requested(monkeypatch):
    class FakeProvider:
        def status(self):
            return {"enabled": True, "available": True, "provider": "fake", "model": "unit-model"}

        def write_advisor_report(self, payload, regenerate=False):
            assert regenerate is True
            return {
                "summary": "Deterministic facts restated by the fake provider.",
                "key_points": [],
                "risks": [],
                "advisor_language": "",
                "data_quality_statement": "",
                "provider": "fake",
                "model": "unit-model",
                "needs_review": True,
            }

    monkeypatch.setattr(ai_copilot, "get_provider", lambda: FakeProvider())
    narrative, meta = report_snapshots.resolve_narrative(
        {"kind": "instrument", "title": "Gen Check"},
        provided_narrative="",
        include_ai_narrative=True,
    )
    assert "Deterministic facts restated by the fake provider." in narrative
    assert meta["status"] == "generated"
    assert meta["provider"]["provider"] == "fake"

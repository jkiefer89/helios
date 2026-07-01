from io import BytesIO

import app as helios
from conftest import price_csv


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_instrument_report_contains_required_sections_and_disclaimer():
    resp = _client().get("/api/report/instrument?ticker=AAPL")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind"] == "instrument"
    assert set(body["sections"]) >= {
        "executive_summary",
        "action",
        "evidence",
        "risk",
        "forecast",
        "strategy",
        "provenance",
        "data_quality",
        "assumptions",
    }
    assert body["eligible_for_real_research"] is False
    assert body["data_mode"] == "demo"
    assert body["title"].startswith("Demo Report")
    assert body["sections"]["provenance"]["source"] == "sample"
    assert body["sections"]["provenance"]["row_count"] > 0
    assert body["sections"]["provenance"]["first_date"]
    assert body["sections"]["provenance"]["last_date"]
    assert body["sections"]["provenance"]["eligible_for_real_research"] is False
    assert "analysis only" in body["disclaimer"].lower()
    assert body["timestamp"]


def test_model_report_includes_clinic_summary_and_simulated_warning(monkeypatch):
    monkeypatch.setattr("engine.data.HAS_YF", False)
    client = _client()
    payload = {
        "file": (BytesIO(b"Ticker,Weight\nFAKEAAA,50\nFAKEBBB,50\n"), "sim-report.csv"),
        "name": "Sim Report",
        "mandate": "balanced",
    }
    upload = client.post("/api/model/upload", data=payload, content_type="multipart/form-data")
    assert upload.status_code == 200
    model_id = upload.get_json()["id"]

    resp = client.get(f"/api/report/model?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind"] == "model"
    assert body["eligible_for_real_research"] is False
    assert body["data_mode"] == "invalid_for_research"
    assert body["title"].startswith("Data Quality Blocked")
    assert "data_quality" in body["sections"]
    assert "clinic" in body["sections"]
    assert any("real price history" in warning.lower() for warning in body["warnings"])
    assert "analysis only" in body["disclaimer"].lower()


def test_real_model_report_includes_analysis_window_and_source_metadata():
    client = _client()
    for symbol in ("UPA", "UPB"):
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
            "file": (BytesIO(b"Ticker,Weight\nUPA,60\nUPB,40\n"), "real-model.csv"),
            "name": "Uploaded Real Model",
            "mandate": "balanced",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]

    resp = client.get(f"/api/report/model?id={model_id}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["kind"] == "model"
    assert body["eligible_for_real_research"] is True
    assert body["data_mode"] == "real"
    assert body["source"] == "upload"
    assert body["data_provenance"]["source"] == "upload"
    assert body["data_provenance"]["source_counts"] == {"upload": 2}
    assert body["data_provenance"]["row_count"] > 200
    assert body["data_provenance"]["first_date"]
    assert body["data_provenance"]["last_date"]
    assert body["sections"]["data_quality"]["row_count"] == body["data_provenance"]["row_count"]
    assert body["sections"]["provenance"]["row_count"] == body["data_provenance"]["row_count"]
    assert body["sections"]["provenance"]["source_counts"] == {"upload": 2}
    assert "analysis only" in body["disclaimer"].lower()


def test_report_error_paths_return_json():
    resp = _client().get("/api/report/instrument?ticker=NOT_A_SAMPLE")

    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_report_snapshot_history_exports_html_and_pdf_with_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "snapshot.csv"),
            "symbol": "SNAP",
            "name": "Snapshot Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    save = client.post(
        "/api/report/snapshots",
        json={
            "kind": "instrument",
            "id": "SNAP",
            "ai_narrative": "Advisor reviewed AI narrative for the saved snapshot.",
        },
    )

    assert save.status_code == 200
    body = save.get_json()
    snapshot = body["snapshot"]
    assert snapshot["id"]
    assert snapshot["target_kind"] == "instrument"
    assert snapshot["target_id"] == "SNAP"
    assert snapshot["source"] == "upload"
    assert snapshot["row_count"] == 260
    assert snapshot["first_date"]
    assert snapshot["last_date"]
    assert snapshot["html_url"].endswith(".html")
    assert snapshot["pdf_url"].endswith(".pdf")
    assert snapshot["ai_narrative_included"] is True
    assert snapshot["ai_narrative_status"] == "provided"
    assert body["storage"]["durable"] is True
    assert body["storage"]["backend"] == "sqlite"

    history = client.get("/api/report/snapshots")
    assert history.status_code == 200
    history_body = history.get_json()
    assert history_body["count"] == 1
    assert history_body["storage"]["durable"] is True
    assert history_body["snapshots"][0]["id"] == snapshot["id"]
    assert history_body["snapshots"][0]["row_count"] == 260
    persistence.reset_store_for_tests()
    reloaded = client.get("/api/report/snapshots").get_json()
    assert reloaded["count"] == 1
    assert reloaded["snapshots"][0]["id"] == snapshot["id"]

    html = client.get(snapshot["html_url"])
    assert html.status_code == 200
    assert html.content_type.startswith("text/html")
    html_text = html.get_data(as_text=True)
    assert "Helios Report Snapshot" in html_text
    assert "Analysis only" in html_text
    assert "Snapshot Upload" in html_text
    assert "Source" in html_text and "upload" in html_text
    assert "Row Count" in html_text and "260" in html_text
    assert "First Date" in html_text
    assert "Last Date" in html_text
    assert "Advisor reviewed AI narrative" in html_text
    assert "investment advice" in html_text.lower()

    pdf = client.get(snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert pdf.data.startswith(b"%PDF-")
    assert b"HELIOS PRO" in pdf.data
    assert b"Advisor-Grade Research Terminal" in pdf.data
    assert b"Helios Report Snapshot" in pdf.data
    assert b"SOURCE AND PROVENANCE" in pdf.data
    assert b"ADVISOR REVIEW REQUIRED" in pdf.data
    assert pdf.data.count(b"/Type /Page ") >= 2


def test_report_snapshot_can_generate_ai_narrative_when_provider_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "ai-snapshot.csv"),
            "symbol": "AISNAP",
            "name": "AI Snapshot Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    class FakeProvider:
        def status(self):
            return {"enabled": True, "available": True, "provider": "fake", "model": "unit-model"}

        def write_advisor_report(self, payload, regenerate=False):
            assert regenerate is True
            assert payload["report"]["kind"] == "instrument"
            assert "price_history" not in str(payload).lower()
            return {
                "summary": "AI narrative summary based only on Helios report facts.",
                "key_points": ["Source and range were reviewed."],
                "risks": ["Forecasts remain estimates."],
                "what_would_invalidate": [],
                "advisor_language": "Advisor language remains analysis-only and requires review.",
                "compliance_caveats": ["Advisor review required before client use."],
                "used_numbers": [],
                "missing_information": [],
                "data_quality_statement": "Real uploaded history is present.",
                "provider": "fake",
                "model": "unit-model",
                "generated_at": "2026-06-30T12:00:00+00:00",
                "needs_review": True,
            }

    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: FakeProvider())

    save = client.post(
        "/api/report/snapshots",
        json={"kind": "instrument", "id": "AISNAP", "include_ai_narrative": True},
    )

    assert save.status_code == 200
    snapshot = save.get_json()["snapshot"]
    assert snapshot["ai_narrative_included"] is True
    assert snapshot["ai_narrative_status"] == "generated"
    assert snapshot["ai_provider"]["provider"] == "fake"
    html_text = client.get(snapshot["html_url"]).get_data(as_text=True)
    assert "AI narrative summary based only on Helios report facts" in html_text
    assert "Advisor Review Required" in html_text


def test_model_report_snapshot_includes_model_metadata_and_source_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    for symbol in ("MDA", "MDB"):
        upload = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(price_csv(days=240)), f"{symbol}.csv"),
                "symbol": symbol,
                "name": f"{symbol} uploaded history",
            },
            content_type="multipart/form-data",
        )
        assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nMDA,55\nMDB,45\n"), "snapshot-model.csv"),
            "name": "Snapshot Model",
            "mandate": "pure_growth",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]

    save = client.post("/api/report/snapshots", json={"kind": "model", "id": model_id})

    assert save.status_code == 200
    snapshot = save.get_json()["snapshot"]
    assert snapshot["target_kind"] == "model"
    assert snapshot["target_id"] == model_id
    assert snapshot["model_metadata"]["mandate"] == "pure_growth"
    assert snapshot["source_counts"] == {"upload": 2}
    assert snapshot["row_count"] >= 200

    html_text = client.get(snapshot["html_url"]).get_data(as_text=True)
    assert "Snapshot Model" in html_text
    assert "Model Metadata" in html_text
    assert "pure_growth" in html_text
    assert "Source Counts" in html_text
    assert "upload" in html_text
    assert "No Return Guarantee" in html_text

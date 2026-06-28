from io import BytesIO

import app as helios


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


def test_report_error_paths_return_json():
    resp = _client().get("/api/report/instrument?ticker=NOT_A_SAMPLE")

    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()

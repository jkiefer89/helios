from io import BytesIO
from datetime import date

import app as helios
from conftest import price_csv, price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_model_report_and_snapshot_preserve_current_aum_capacity_context(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=320)), "aum-aapl.csv"),
            "symbol": "AAPL",
            "name": "AAPL AUM report history",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,100\n"), "aum-model.csv"),
            "name": "AUM Capacity Model",
            "mandate": "pure_growth",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]
    as_of = date.today().isoformat()

    report = client.get(
        f"/api/report/model?id={model_id}&aum_usd=1000000&aum_as_of={as_of}"
    )

    assert report.status_code == 200
    capital = report.get_json()["sections"]["capital_context"]
    assert capital["aum_usd"] == 1_000_000
    assert capital["aum_as_of"] == as_of
    assert capital["capacity_status"] in {"within_capacity", "watch"}
    assert capital["capacity_unsized_count"] == 0
    assert capital["capacity_proxy_based_count"] == 1

    save = client.post(
        "/api/report/snapshots",
        json={
            "kind": "model",
            "id": model_id,
            "aum_usd": 1_000_000,
            "aum_as_of": as_of,
            "report_purpose": "advisor_review",
        },
    )

    assert save.status_code == 200
    snapshot_capital = save.get_json()["snapshot"]["capital_context"]
    assert snapshot_capital["aum_usd"] == 1_000_000
    assert snapshot_capital["aum_as_of"] == as_of
    assert snapshot_capital["capacity_status"] in {"within_capacity", "watch"}

    missing_aum = client.post(
        "/api/report/snapshots",
        json={"kind": "model", "id": model_id, "report_purpose": "client_review"},
    )
    assert missing_aum.status_code == 409
    assert "current as-of AUM" in missing_aum.get_json()["error"]

    proxy_capacity = client.post(
        "/api/report/snapshots",
        json={
            "kind": "model", "id": model_id, "report_purpose": "investment_committee",
            "aum_usd": 1_000_000, "aum_as_of": as_of,
        },
    )
    assert proxy_capacity.status_code == 409
    assert "observed market volume" in proxy_capacity.get_json()["error"]

    stale_aum = client.post(
        "/api/report/snapshots",
        json={
            "kind": "model", "id": model_id, "report_purpose": "client_review",
            "aum_usd": 1_000_000, "aum_as_of": "2000-01-01",
        },
    )
    assert stale_aum.status_code == 400
    assert "refresh it within" in stale_aum.get_json()["error"]


def test_model_client_export_gate_requires_current_sized_capacity(monkeypatch):
    from engine import portfolio
    from helios_web import reports

    model = portfolio.Model(
        id="CLIENT-CAP", name="Client Capacity", mandate_key="balanced",
        mandate_context="", holdings=[portfolio.Holding("AAPL", 1.0)],
    )
    portfolio.register(model)
    monkeypatch.setattr(
        reports.research_gate, "model_readiness",
        lambda _model: {"passed": True, "state": "pass", "blocked_reason": ""},
    )
    monkeypatch.setattr(
        reports.model_governance, "payload",
        lambda _models: {"models": [{"id": model.id, "approval_status": "approved"}]},
    )

    missing = reports._client_export_gate("model", model.id, report={"sections": {}})
    assert missing["passed"] is False
    assert "current as-of AUM" in missing["blocked_reason"]

    ready = reports._client_export_gate("model", model.id, report={
        "sections": {"capital_context": {
            "aum_usd": 25_000_000,
            "aum_as_of": date.today().isoformat(),
            "capacity_status": "within_capacity",
            "capacity_unsized_count": 0,
            "capacity_proxy_based_count": 0,
        }},
    })
    assert ready["passed"] is True

    proxy_only = reports._client_export_gate("model", model.id, report={
        "sections": {"capital_context": {
            "aum_usd": 25_000_000,
            "aum_as_of": date.today().isoformat(),
            "capacity_status": "within_capacity",
            "capacity_unsized_count": 0,
            "capacity_proxy_based_count": 1,
        }},
    })
    assert proxy_only["passed"] is False
    assert "observed market volume" in proxy_only["blocked_reason"]


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
        "research_context",
    }
    assert body["eligible_for_real_research"] is False
    assert body["data_mode"] == "invalid_for_research"
    assert body["title"].startswith("Data Quality Blocked")
    assert body["sections"]["provenance"]["source"] == "sample"
    assert body["sections"]["provenance"]["row_count"] > 0
    assert body["sections"]["provenance"]["first_date"]
    assert body["sections"]["provenance"]["last_date"]
    assert body["sections"]["provenance"]["eligible_for_real_research"] is False
    assert body["sections"]["provenance"]["freshness"]["latest_bar_date"]
    assert body["sections"]["research_context"]["configured"] is False
    assert body["sections"]["strategy"]["current_signal"]["action_label"]
    assert body["sections"]["strategy"]["path_evidence"]["date_range"]["session_count"] > 0
    assert body["sections"]["strategy"]["oos_evidence"]["status"] == "ok"
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
    assert "last_refresh" not in body["sections"]["provenance"]
    assert body["sections"]["provenance"]["freshness"]["component_count"] == 2
    assert body["sections"]["provenance"]["freshness"]["binding_latest_bar_date"]
    assert body["sections"]["research_context"]["target_kind"] == "model"
    assert body["sections"]["strategy"]["current_signal"]["action_label"]
    assert body["sections"]["strategy"]["oos_evidence"]["status"] == "insufficient_data"
    assert "analysis only" in body["disclaimer"].lower()


def test_instrument_report_includes_saved_governed_context(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    uploaded = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=320)), "context-report.csv"),
            "symbol": "CTXREP",
            "name": "Context Report",
        },
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    saved = client.post("/api/research-context", json={
        "target_id": "CTXREP",
        "thesis": "Hold while durable earnings growth and relative strength support the stated mandate.",
        "mandate_key": "balanced",
        "benchmark": "SPY",
        "horizon_days": 42,
        "invalidation_criteria": ["Relative strength remains below SPY for two reviews."],
        "change_note": "Create context for report evidence.",
    })
    assert saved.status_code == 200

    report = client.get("/api/report/instrument?ticker=CTXREP")

    assert report.status_code == 200
    context = report.get_json()["sections"]["research_context"]
    assert context["configured"] is True
    assert context["benchmark"] == "SPY"
    assert context["horizon_days"] == 42
    assert context["invalidation_criteria"] == ["Relative strength remains below SPY for two reviews."]


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
    assert "Snapshot Upload" in html_text
    assert "Source" in html_text and "upload" in html_text
    assert "Row Count" in html_text and "260" in html_text
    assert "First Date" in html_text
    assert "Last Date" in html_text
    assert "Advisor reviewed AI narrative" in html_text
    assert "investment advice" not in html_text.lower()

    pdf = client.get(snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert pdf.data.startswith(b"%PDF-")
    assert b"HELIOS PRO" in pdf.data
    assert b"Advisor-Grade Research Terminal" in pdf.data
    assert b"Helios Report Snapshot" in pdf.data
    assert b"SOURCE AND PROVENANCE" in pdf.data
    assert b"ADVISOR REVIEW REQUIRED" not in pdf.data
    assert pdf.data.count(b"/Type /Page") >= 2


def test_institutional_report_snapshots_include_versions_audit_and_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "institutional.csv"),
            "symbol": "INST",
            "name": "Institutional Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    first = client.post(
        "/api/report/snapshots",
        json={
            "kind": "instrument",
            "id": "INST",
            "prepared_for": "Investment Committee",
            "prepared_by": "Helios Advisor Desk",
            "reviewer": "Chief Investment Officer",
            "report_purpose": "client_review",
        },
    )
    assert first.status_code == 200
    first_snapshot = first.get_json()["snapshot"]
    assert first_snapshot["report_package"] == "institutional_advisor_report"
    assert first_snapshot["version"] == 1
    assert first_snapshot["version_label"] == "v1"
    assert first_snapshot["prepared_for"] == "Investment Committee"
    assert first_snapshot["prepared_by"] == "Helios Advisor Desk"
    assert first_snapshot["reviewer"] == "Chief Investment Officer"
    assert first_snapshot["report_purpose"] == "client_review"
    assert first_snapshot["output_formats"] == ["html", "pdf", "print"]
    assert first_snapshot["metadata"]["snapshot_version"] == 2
    assert first_snapshot["metadata"]["institutional_report"] is True
    assert first_snapshot["audit_trail"][0]["event"] == "report_built"
    assert first_snapshot["audit_trail"][-1]["event"] == "snapshot_saved"
    assert "disclosure_blocks" not in first_snapshot

    second = client.post("/api/report/snapshots", json={"kind": "instrument", "id": "INST"})
    assert second.status_code == 200
    second_snapshot = second.get_json()["snapshot"]
    assert second_snapshot["version"] == 2
    assert second_snapshot["version_label"] == "v2"

    history = client.get("/api/report/snapshots").get_json()["snapshots"]
    assert [row["version"] for row in history[:2]] == [2, 1]

    html = client.get(first_snapshot["html_url"])
    assert html.status_code == 200
    html_text = html.get_data(as_text=True)
    assert "Institutional Advisor Report" in html_text
    assert "Report Version" in html_text and "v1" in html_text
    assert "Audit Trail" in html_text
    assert "Source And Provenance" in html_text
    assert "Investment Committee" in html_text
    assert "Helios Advisor Desk" in html_text
    assert "Disclosure Blocks" not in html_text

    pdf = client.get(first_snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.content_type == "application/pdf"
    assert b"INSTITUTIONAL ADVISOR REPORT" in pdf.data
    assert b"REPORT VERSION" in pdf.data
    assert b"AUDIT TRAIL" in pdf.data
    assert b"DISCLOSURE BLOCKS" not in pdf.data


def test_institutional_pdf_export_is_full_designer_grade_package(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "designer-grade.csv"),
            "symbol": "DESIGN",
            "name": "Designer Grade Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    save = client.post(
        "/api/report/snapshots",
        json={
            "kind": "instrument",
            "id": "DESIGN",
            "prepared_for": "Client Investment Committee",
            "prepared_by": "Helios Advisory",
            "reviewer": "Portfolio Oversight",
            "report_purpose": "client_review",
        },
    )
    assert save.status_code == 200
    snapshot = save.get_json()["snapshot"]
    assert snapshot["metadata"]["pdf_engine"] == "reportlab"
    assert snapshot["metadata"]["pdf_layout"] == "designer_grade_institutional"

    pdf = client.get(snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.data.startswith(b"%PDF-")
    assert pdf.data.count(b"/Type /Page") >= 4
    assert b"APPROVED EXTERNAL REVIEW PACK" in pdf.data
    assert b"INSTITUTIONAL ADVISOR REPORT" in pdf.data
    assert b"EXECUTIVE SUMMARY" in pdf.data
    assert b"PROVENANCE DASHBOARD" in pdf.data
    assert b"EVIDENCE DETAIL" in pdf.data
    assert b"DISCLOSURE BLOCKS" not in pdf.data
    assert b"Page 1 of" in pdf.data
    assert b"Page 4 of" in pdf.data


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
                "advisor_language": "The current evidence supports continued monitoring.",
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
    assert "Advisor Review Required" not in html_text


def test_cloud_report_narrative_runs_from_sanitized_payload_without_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    upload = client.post(
        "/api/upload",
        data={
            "file": (BytesIO(price_csv(days=260)), "cloud-report.csv"),
            "symbol": "CLOUDREP",
            "name": "Cloud Report Upload",
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    class CloudProvider:
        provider = "anthropic"
        mode = "cloud"
        model = "test-cloud-model"

        def __init__(self):
            self.calls = 0

        def status(self):
            return {
                "enabled": True, "available": True, "provider": self.provider,
                "mode": self.mode, "model": self.model,
            }

        def write_advisor_report(self, payload, regenerate=False):
            self.calls += 1
            assert regenerate is True
            assert payload["report"]["kind"] == "instrument"
            return {
                "summary": "Cloud narrative from sanitized Helios facts.",
                "key_points": [], "risks": ["Source quality can change."],
                "what_would_invalidate": [], "advisor_language": "Monitor the supplied risk evidence.",
                "used_numbers": [],
                "missing_information": [], "data_quality_statement": "Uploaded history.",
                "provider": self.provider, "model": self.model, "needs_review": True,
            }

    provider = CloudProvider()
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)
    request_body = {
        "kind": "instrument", "id": "CLOUDREP", "include_ai_narrative": True,
    }

    response = client.post("/api/report/snapshots", json=request_body)

    assert response.status_code == 200, response.get_json()
    assert provider.calls == 1
    snapshot = response.get_json()["snapshot"]
    assert snapshot["ai_narrative_status"] == "generated"
    transfer = snapshot["ai_provider"]["cloud_transfer"]
    assert transfer["task"] == "report_narrative"
    assert transfer["provider"] == "anthropic"
    assert transfer["model"] == "test-cloud-model"
    assert transfer["confirmed"] is True
    assert transfer["confirmation_required"] is False
    assert transfer["raw_values_returned"] is False


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
    assert "No Return Guarantee" not in html_text


def test_model_report_snapshot_includes_client_grade_risk_pack(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import persistence

    persistence.reset_store_for_tests()
    client = _client()
    for symbol, daily in (("QQQ", 0.0005), ("AAPL", 0.0014), ("LMT", 0.0006), ("GLD", 0.0002)):
        series = price_series(days=320, daily=daily)
        upload = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(
                    f"Date,Close\n".encode("utf-8")
                    + b"".join(f"{date.strftime('%Y-%m-%d')},{value}\n".encode("utf-8") for date, value in series.items())
                ), f"{symbol}.csv"),
                "symbol": symbol,
                "name": f"{symbol} risk pack history",
            },
            content_type="multipart/form-data",
        )
        assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nAAPL,60\nLMT,25\nGLD,15\n"), "risk-pack-model.csv"),
            "name": "Risk Pack Model",
            "mandate": "pure_growth",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]

    report = client.get(f"/api/report/model?id={model_id}")

    assert report.status_code == 200
    report_body = report.get_json()
    pack = report_body["sections"]["client_risk_pack"]
    assert pack["available"] is True
    assert pack["summary"]["model_id"] == model_id
    assert pack["stress_scenarios"]
    assert pack["historical_stress_replay"]
    assert pack["historical_stress_replay"][0]["basis"] == "observed_model_history"
    assert pack["benchmark_relative_drawdown"]["benchmark_symbol"] == "QQQ"
    assert pack["concentration_warnings"]
    assert pack["liquidity_flags"]["summary"]["basis"]
    assert pack["correlation_clusters"]
    assert pack["what_would_break_this_model"]
    assert all(row["evidence"] for row in pack["what_would_break_this_model"])
    assert all(row["trigger"] for row in pack["what_would_break_this_model"])
    assert "analysis only" in pack["disclaimer"].lower()

    save = client.post("/api/report/snapshots", json={"kind": "model", "id": model_id})

    assert save.status_code == 200
    snapshot = save.get_json()["snapshot"]
    assert snapshot["client_risk_pack"]["available"] is True

    html_text = client.get(snapshot["html_url"]).get_data(as_text=True)
    assert "Risk Evidence" in html_text
    assert "Client Risk Pack" not in html_text
    assert "Internal risk evidence from the current model history" in html_text
    assert "Historical Stress Replay" in html_text
    assert "What Would Break This Model" in html_text
    assert "Benchmark Relative Drawdown" in html_text
    assert "Observed Model History" in html_text
    assert "Growth multiple compression" in html_text

    pdf = client.get(snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert b"RISK EVIDENCE" in pdf.data
    assert b"CLIENT-GRADE RISK PACK" not in pdf.data
    assert b"HISTORICAL STRESS REPLAY" in pdf.data
    assert b"WHAT WOULD BREAK THIS MODEL" in pdf.data


def test_report_snapshot_embeds_signal_journal_evidence_record(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    from engine import data, persistence, portfolio, signal_journal

    persistence.reset_store_for_tests()
    client = _client()
    for symbol in ("JNA", "JNB"):
        upload = client.post(
            "/api/upload",
            data={
                "file": (BytesIO(price_csv(days=260)), f"{symbol}.csv"),
                "symbol": symbol,
                "name": f"{symbol} journal history",
            },
            content_type="multipart/form-data",
        )
        assert upload.status_code == 200
    model_upload = client.post(
        "/api/model/upload",
        data={
            "file": (BytesIO(b"Ticker,Weight\nJNA,55\nJNB,45\n"), "journal-model.csv"),
            "name": "Journal Evidence Model",
            "mandate": "pure_growth",
        },
        content_type="multipart/form-data",
    )
    assert model_upload.status_code == 200
    model_id = model_upload.get_json()["id"]

    mdl = portfolio.get(model_id)
    model_close = portfolio.build_series(mdl).close
    benchmark_close = price_series(days=260, start=100.0, daily=0.0005)
    data.register(data.Instrument("BENCHJ", "Benchmark Journal", benchmark_close.to_frame("close"), "live", []))
    signal_journal.record_signal(
        target_kind="model",
        target_id=model_id,
        target_name="Journal Evidence Model",
        # Input ends 3 business days ago (within the 7-day settlement guard)
        # and the remaining bars cover the short horizon -> measurable now.
        close=model_close,
        input_close=model_close.iloc[:-3],
        signal={"action": "BUY", "score": 0.64},
        horizon_days=3,
        benchmark="BENCHJ",
        source_counts={"upload": 2},
        eligible_for_real_research=True,
        data_mode="real",
    )
    signal_journal.record_signal(
        target_kind="model",
        target_id=model_id,
        target_name="Journal Evidence Model",
        close=model_close,
        input_close=model_close,
        signal={"action": "HOLD", "score": 0.22},
        horizon_days=10,
        benchmark="BENCHJ",
        source_counts={"upload": 2},
        eligible_for_real_research=True,
        data_mode="real",
    )

    save = client.post("/api/report/snapshots", json={"kind": "model", "id": model_id})

    assert save.status_code == 200
    snapshot = save.get_json()["snapshot"]
    evidence = snapshot["signal_journal"]
    assert evidence["summary"]["total_count"] == 2
    assert evidence["summary"]["measured_count"] == 1
    assert evidence["summary"]["pending_count"] == 1
    assert evidence["summary"]["hit_rate_pct"] is not None
    assert evidence["summary"]["avg_alpha_pct"] is not None
    assert {row["forward_status"] for row in evidence["target_history"]} == {"measured", "pending"}
    assert evidence["benchmark_comparison"][0]["benchmark"] == "BENCHJ"
    model_row = evidence["model_credibility"][0]
    assert model_row["target_id"] == model_id
    assert model_row["signal_count"] == 2
    assert model_row["measured_count"] == 1
    assert model_row["pending_count"] == 1
    assert model_row["avg_alpha_pct"] is not None
    assert evidence["methodology"]["paper_tracking_only"] is True

    history = client.get("/api/report/snapshots").get_json()["snapshots"]
    assert history[0]["signal_journal"]["summary"]["total_count"] == 2

    html_text = client.get(snapshot["html_url"]).get_data(as_text=True)
    assert "Signal Journal Evidence" in html_text
    assert "Forward Results" in html_text
    assert "Hit Rate" in html_text
    assert "Alpha Vs Benchmark" in html_text
    assert "Model-By-Model Credibility" in html_text
    assert "Journal Evidence Model" in html_text

    pdf = client.get(snapshot["pdf_url"])
    assert pdf.status_code == 200
    assert b"SIGNAL JOURNAL EVIDENCE" in pdf.data
    assert b"FORWARD RESULTS" in pdf.data
    assert b"HIT RATE" in pdf.data
    assert b"ALPHA VS BENCHMARK" in pdf.data
    assert b"MODEL-BY-MODEL CREDIBILITY" in pdf.data

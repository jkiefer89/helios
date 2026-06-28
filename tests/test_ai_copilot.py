import json
from datetime import datetime, timezone

import pytest

import app as helios
from engine import ai_copilot, persistence


@pytest.fixture()
def client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def _config(**overrides):
    values = {
        "enabled": True,
        "provider": "anthropic",
        "local_backend": "ollama",
        "local_base_url": "http://127.0.0.1:11434",
        "local_model": "",
        "timeout_s": 1.0,
        "local_require_localhost": True,
        "anthropic_key_present": False,
        "anthropic_model": "test-claude",
        "openai_key_present": False,
        "openai_model": "test-openai",
        "redact_client_names": True,
        "send_holdings": False,
        "cache_ttl_s": 0,
    }
    values.update(overrides)
    return ai_copilot.AIConfig(**values)


class FakeProvider:
    def __init__(self, provider="anthropic"):
        self.provider = provider
        self.model = "fake-model"

    def status(self):
        return {
            "enabled": True,
            "provider": self.provider,
            "mode": "cloud" if self.provider != "local" else "local",
            "model": self.model,
            "available": True,
            "reason": "fake provider ready",
            "privacy_warning": "Sends sanitized Helios metrics to Anthropic Claude.",
            "security_warnings": [],
            "keys_exposed": False,
            "secrets_stored": False,
        }

    def _result(self, task):
        return {
            "summary": "Review candidate from supplied Helios facts only.",
            "key_points": ["Score 42 was supplied by Helios."],
            "risks": ["Evidence can change when data source changes."],
            "what_would_invalidate": ["A failed provenance gate."],
            "advisor_language": "Analysis only; review the Helios evidence pack before any action.",
            "compliance_caveats": ["No return guarantee."],
            "used_numbers": ["42"],
            "missing_information": ["Client suitability outside Helios."],
            "data_quality_statement": "Real data status depends on the payload provenance.",
            "provider": self.provider,
            "model": self.model,
            "task": task,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def explain_opportunity(self, payload, regenerate=False):
        return self._result("opportunity_explain")

    def critique_opportunity(self, payload, regenerate=False):
        return self._result("opportunity_critique")

    def summarize_strategy(self, payload, regenerate=False):
        return self._result("strategy_summary")

    def summarize_portfolio_clinic(self, payload, regenerate=False):
        return self._result("clinic_summary")

    def write_advisor_report(self, payload, regenerate=False):
        return self._result("report_narrative")

    def answer_question(self, payload, question, regenerate=False):
        result = self._result("question")
        result["summary"] = f"Answered using supplied Helios facts: {question}"
        return result


def test_ai_disabled_status(client, monkeypatch):
    monkeypatch.setenv("HELIOS_AI_ENABLED", "0")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "none")

    resp = client.get("/api/ai/status")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["available"] is False
    assert body["provider"] == "none"
    assert "off" in body["reason"].lower()


def test_claude_missing_key_status_and_endpoint_503(client, monkeypatch):
    monkeypatch.setenv("HELIOS_AI_ENABLED", "1")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    status = client.get("/api/ai/status").get_json()
    resp = client.post("/api/ai/opportunity/explain", json={"payload": {"symbol": "AAPL", "score": 42}})

    assert status["provider"] == "anthropic"
    assert status["available"] is False
    assert resp.status_code == 503
    assert "ANTHROPIC_API_KEY" in resp.get_json()["error"]


def test_claude_fake_provider_success(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TEST_ANTHROPIC_KEY_NEVER_RETURNED")
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: FakeProvider("anthropic"))

    resp = client.post(
        "/api/ai/opportunity/explain",
        json={"payload": {"symbol": "AAPL", "score": 42, "data_mode": "real"}},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["provider"] == "anthropic"
    assert body["result"]["summary"]
    assert "TEST_ANTHROPIC_KEY_NEVER_RETURNED" not in json.dumps(body)


def test_provider_timeout_returns_503(client, monkeypatch):
    class TimeoutProvider(FakeProvider):
        def explain_opportunity(self, payload, regenerate=False):
            raise ai_copilot.AITimeoutError("Provider timed out.", self.status())

    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: TimeoutProvider())

    resp = client.post("/api/ai/opportunity/explain", json={"payload": {"symbol": "AAPL"}})

    assert resp.status_code == 503
    assert "timed out" in resp.get_json()["error"].lower()


def test_malformed_json_is_review_required():
    class MalformedProvider(ai_copilot.AIProvider):
        provider = "fake"
        mode = "test"

        def __init__(self):
            super().__init__(_config())
            self.model = "fake-model"

        def status(self):
            return {
                "enabled": True,
                "provider": "fake",
                "mode": "test",
                "model": self.model,
                "available": True,
                "reason": "ready",
                "keys_exposed": False,
                "secrets_stored": False,
            }

        def _complete(self, prompt):
            return "not json"

    result = MalformedProvider().explain_opportunity({"data_mode": "real", "score": 42})

    assert result["needs_review"] is True
    assert result["malformed_json"] is True


def test_unsupported_numeric_claim_detection():
    result = ai_copilot.validate_ai_output(
        {"summary": "Helios supplied 12 but the narrative says 99.", "advisor_language": "Review only."},
        {"score": 12, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["needs_review"] is True
    assert "99" in result["unsupported_numbers"]


def test_forbidden_guarantee_phrase_detection():
    result = ai_copilot.validate_ai_output(
        {"summary": "This is guaranteed and risk-free.", "advisor_language": "No downside."},
        {"score": 42, "data_mode": "real"},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["needs_review"] is True
    assert "guaranteed" in result["blocked_phrases"]
    assert "risk-free" in result["blocked_phrases"]
    assert "no downside" in result["blocked_phrases"]
    assert "[unsupported assurance removed]" in result["summary"]
    assert "[unsupported assurance removed]" in result["advisor_language"]


def test_api_keys_never_appear_in_status_or_response(client, monkeypatch):
    sentinel = "TEST_SECRET_SENTINEL_VALUE"
    monkeypatch.setenv("HELIOS_AI_ENABLED", "1")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)

    status = client.get("/api/ai/status")
    assert status.status_code == 200
    assert sentinel not in json.dumps(status.get_json())

    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: FakeProvider("anthropic"))
    resp = client.post("/api/ai/question", json={"payload": {"score": 42}, "question": "What matters?"})
    assert resp.status_code == 200
    assert sentinel not in json.dumps(resp.get_json())


def test_sanitization_redacts_names_and_omits_raw_files_and_history():
    payload = {
        "client_name": "Jane Client",
        "model_name": "Growth Model",
        "raw": "raw uploaded bytes",
        "csv": "Date,Close",
        "price_history": [1, 2, 3],
        "series": {"dates": ["2026-01-01"], "close": [100.0]},
        "score": 42,
    }

    sanitized = ai_copilot.sanitize_payload(payload, _config())
    text = json.dumps(sanitized)

    assert "Jane Client" not in text
    assert "Growth Model" not in text
    assert "raw uploaded bytes" not in text
    assert "Date,Close" not in text
    assert "2026-01-01" not in text
    assert sanitized["client_name"] == "[redacted]"
    assert sanitized["_sanitization"]["full_price_history_sent"] is False


def test_holdings_are_omitted_unless_enabled():
    payload = {"holdings": [{"ticker": "AAPL", "weight": 0.7}], "score": 42}

    omitted = ai_copilot.sanitize_payload(payload, _config(send_holdings=False))
    included = ai_copilot.sanitize_payload(payload, _config(send_holdings=True))

    assert omitted["holdings"]["omitted"] is True
    assert "AAPL" not in json.dumps(omitted)
    assert included["holdings"][0]["ticker"] == "AAPL"


def test_demo_and_blocked_payloads_force_caveats():
    demo = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Demo sample."},
        {"data_mode": "demo", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )
    blocked = ai_copilot.validate_ai_output(
        {"summary": "Review candidate.", "data_quality_statement": "Blocked."},
        {"data_mode": "invalid_for_research", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert "not real market evidence" in demo["data_quality_statement"].lower()
    assert "not real market evidence" in blocked["data_quality_statement"].lower()


def test_ai_cannot_override_hold_or_review_action():
    result = ai_copilot.validate_ai_output(
        {"summary": "This should be a buy candidate.", "advisor_language": "Strong buy."},
        {"action": "HOLD", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["needs_review"] is True
    assert result["deterministic_action"] == "HOLD"
    assert any("may not upgrade" in caveat for caveat in result["compliance_caveats"])


def test_ai_keys_are_not_stored_in_sqlite(client, monkeypatch, tmp_path):
    sentinel = "TEST_SECRET_SQLITE_SENTINEL"
    db_path = tmp_path / "helios.db"
    monkeypatch.setenv("HELIOS_DB_PATH", str(db_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    persistence.reset_store_for_tests()

    store = persistence.get_store()
    assert store.status()["available"] is True
    client.get("/api/ai/status")

    for path in tmp_path.iterdir():
        if path.is_file():
            assert sentinel.encode("utf-8") not in path.read_bytes()


def test_report_narrative_includes_analysis_only_caveat():
    result = ai_copilot.validate_ai_output(
        {"summary": "Advisor narrative from supplied facts."},
        {"data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "report_narrative",
    )

    assert result["needs_review"] is True
    assert any("Analysis only" in caveat for caveat in result["compliance_caveats"])

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
        self.mode = "cloud" if provider != "local" else "local"
        self.model = "fake-model"
        self.last_payload = None
        self.last_question = None
        self.last_messages = None

    def status(self):
        return {
            "enabled": True,
            "provider": self.provider,
            "mode": self.mode,
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
            "advisor_language": "Review the Helios evidence pack before changing the position.",
            "used_numbers": ["42"],
            "missing_information": ["Client suitability outside Helios."],
            "data_quality_statement": "Real data status depends on the payload provenance.",
            "provider": self.provider,
            "model": self.model,
            "task": task,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def explain_opportunity(self, payload, regenerate=False):
        self.last_payload = payload
        return self._result("opportunity_explain")

    def critique_opportunity(self, payload, regenerate=False):
        return self._result("opportunity_critique")

    def summarize_strategy(self, payload, regenerate=False):
        return self._result("strategy_summary")

    def summarize_portfolio_clinic(self, payload, regenerate=False):
        return self._result("clinic_summary")

    def write_advisor_report(self, payload, regenerate=False):
        return self._result("report_narrative")

    def macro_brief(self, payload, regenerate=False):
        return self._result("macro_brief")

    def answer_question(self, payload, question, regenerate=False):
        self.last_payload = payload
        self.last_question = question
        result = self._result("question")
        result["summary"] = f"Answered using supplied Helios facts: {question}"
        return result

    def chat(self, messages, payload):
        self.last_messages = messages
        self.last_payload = payload
        return {
            "reply": "Dialogue response from supplied Helios facts.",
            "provider": self.provider,
            "model": self.model,
        }


def _direct_cloud_post(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    disclosure = body["cloud_transfer"]
    assert disclosure["cloud_transfer"] is True
    assert disclosure["confirmed"] is True
    assert disclosure["confirmation_required"] is False
    assert disclosure["authorization_basis"] == "server_configured_provider"
    return body, response


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

    body, resp = _direct_cloud_post(
        client,
        "/api/ai/opportunity/explain",
        {"payload": {"symbol": "AAPL", "score": 42, "data_mode": "real"}},
    )

    assert resp.status_code == 200
    assert body["provider"] == "anthropic"
    assert body["result"]["summary"]
    assert body["cloud_transfer"]["confirmed"] is True
    assert body["cloud_transfer"]["raw_values_returned"] is False
    assert "disclaimer" not in body
    assert "TEST_ANTHROPIC_KEY_NEVER_RETURNED" not in json.dumps(body)


@pytest.mark.parametrize(("path", "request_body", "expected_task"), (
    ("/api/ai/opportunity/explain", {"payload": {"score": 42}}, "opportunity_explain"),
    ("/api/ai/opportunity/critique", {"payload": {"score": 42}}, "opportunity_critique"),
    ("/api/ai/strategy/summary", {"payload": {"score": 42}}, "strategy_summary"),
    ("/api/ai/clinic/summary", {"payload": {"score": 42}}, "clinic_summary"),
    ("/api/ai/report", {"payload": {"score": 42}}, "report_narrative"),
    ("/api/ai/question", {"payload": {"score": 42}, "question": "Why?"}, "question"),
    ("/api/ai/macro/brief", {"payload": {"score": 42}}, "macro_brief"),
))
def test_cloud_transfer_task_matches_provider_prompt(
    client, monkeypatch, path, request_body, expected_task,
):
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: FakeProvider("anthropic"))

    body, _response = _direct_cloud_post(client, path, request_body)

    assert body["cloud_transfer"]["task"] == expected_task
    assert body["result"]["task"] == expected_task


def test_provider_timeout_returns_503(client, monkeypatch):
    class TimeoutProvider(FakeProvider):
        def explain_opportunity(self, payload, regenerate=False):
            raise ai_copilot.AITimeoutError("Provider timed out.", self.status())

    provider = TimeoutProvider()
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)

    resp = client.post(
        "/api/ai/opportunity/explain", json={"payload": {"symbol": "AAPL"}},
    )

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


def test_prompt_schema_omits_compliance_boilerplate():
    sanitized = ai_copilot.sanitize_payload({
        "data_mode": "real",
        "score": 42,
        "disclaimer": "Analysis only; not investment advice or order execution.",
        "nested": {
            "compliance_caveats": ["More boilerplate."],
            "forecast": {
                "disclaimer": (
                    "Strategic statistical projection, not a trading forecast or guarantee. "
                    "Model quality remains uncertain."
                ),
            },
        },
    }, _config())
    prompt = ai_copilot.build_prompt("opportunity_explain", sanitized)
    request = json.loads(prompt["user"])

    assert "compliance_caveats" not in request["schema"]
    assert "analysis_only_disclaimer" not in request["payload"]
    assert "disclaimer" not in request["payload"]
    assert "compliance_caveats" not in request["payload"]["nested"]
    assert "Model quality remains uncertain." in request["payload"]["nested"]["forecast"]["disclaimer"]


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

    provider = FakeProvider("anthropic")
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)
    _body, resp = _direct_cloud_post(
        client,
        "/api/ai/question",
        {"payload": {"score": 42}, "question": "What matters?"},
    )
    assert resp.status_code == 200
    assert sentinel not in json.dumps(resp.get_json())


def test_cloud_transfer_redacts_sensitive_values_before_provider_call(client, monkeypatch):
    provider = FakeProvider("anthropic")
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)
    secret = "sk-" + "ant-api03-FAKE-DLP-TEST-SENTINEL"
    request_payload = {
        "payload": {
            "clientName": "Jane Private",
            "accountName": "Private Family Account",
            "preparedFor": "Jane Private",
            "score": 42,
            "notes": f"Contact jane.private@example.com and never expose {secret}",
        },
        "question": (
            "Review Jane Private and the Private Family Account; "
            "account number: 123456789."
        ),
    }

    body, response = _direct_cloud_post(client, "/api/ai/question", request_payload)

    assert response.status_code == 200
    assert body["cloud_transfer"]["redaction_count"] >= 3
    transferred = json.dumps({"payload": provider.last_payload, "question": provider.last_question})
    assert "Jane Private" not in transferred
    assert "jane.private@example.com" not in transferred
    assert "Private Family Account" not in transferred
    assert secret not in transferred
    assert "123456789" not in transferred
    assert "[redacted:" in transferred
    assert body["cloud_transfer"]["task"] == "question"
    assert body["cloud_transfer"]["transfer_scope"] == "final_sanitized_provider_request"
    assert body["cloud_transfer"]["disclosure_hash"] != body["cloud_transfer"]["dlp_payload_hash"]


def test_legacy_cloud_confirmation_is_ignored_and_request_runs_once(client, monkeypatch):
    provider = FakeProvider("anthropic")
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)
    payload = {"payload": {"score": 42}, "question": "Explain the evidence."}

    response = client.post("/api/ai/question", json={
        **payload,
        "cloud_confirmation": {"confirmed": True, "disclosure_hash": "wrong-payload-hash"},
    })

    assert response.status_code == 200
    assert provider.last_payload == {"score": 42}
    assert response.get_json()["cloud_transfer"]["confirmation_required"] is False


def test_cloud_chat_redacts_contextual_client_name_without_structured_identity(client, monkeypatch):
    provider = FakeProvider("anthropic")
    monkeypatch.setattr(helios.ai_copilot, "get_provider", lambda: provider)
    request_payload = {
        "payload": {"score": 42},
        "messages": [{
            "role": "user",
            "content": (
                "Explain the evidence for my client Jane Private without adding facts. "
                "Please review for John Smith household. Prepared for Smith Family Trust."
            ),
        }],
    }

    body, response = _direct_cloud_post(client, "/api/ai/chat", request_payload)

    assert response.status_code == 200
    assert "disclaimer" not in body
    transferred = json.dumps(provider.last_messages)
    assert "Jane Private" not in transferred
    assert "John Smith" not in transferred
    assert "Smith Family Trust" not in transferred
    assert "[redacted:client_name]" in transferred
    assert body["cloud_transfer"]["redaction_categories"]["client_name"] >= 3


def test_cloud_transfer_hash_binds_provider_model_task_and_final_request():
    provider = FakeProvider("anthropic")
    value = {"payload": {"score": 42}, "question": "Explain the evidence."}

    _safe, first = ai_copilot.prepare_provider_transfer(
        provider, value, task="question",
    )
    _safe, changed_task = ai_copilot.prepare_provider_transfer(
        provider, value, task="opportunity_explain",
    )
    provider.model = "different-model"
    _safe, changed_model = ai_copilot.prepare_provider_transfer(
        provider, value, task="question",
    )

    hashes = {
        first["disclosure_hash"],
        changed_task["disclosure_hash"],
        changed_model["disclosure_hash"],
    }
    assert len(hashes) == 3
    assert all(not row["confirmation_required"] for row in (first, changed_task, changed_model))


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


def test_demo_and_blocked_payloads_force_data_quality_warnings():
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


def test_ai_disagreement_is_surfaced_not_blocked():
    """Owner contract: the AI may argue against the engine's action; dissent is
    recorded explicitly (never censored) and the deterministic action is never
    edited. Disagreement alone must NOT flag needs_review."""
    result = ai_copilot.validate_ai_output(
        {"summary": "Evidence points the other way.",
         "stance": "DISAGREE: valuation gap and revisions support adding exposure despite the HOLD.",
         "advisor_language": "Strong buy."},
        {"action": "HOLD", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["deterministic_action"] == "HOLD"      # engine action untouched
    assert result["ai_disagrees_with_action"] is True    # dissent surfaced
    assert result["needs_review"] is False               # dissent is not a violation


def test_upgrade_language_without_stance_still_marks_disagreement():
    result = ai_copilot.validate_ai_output(
        {"summary": "This should be a buy candidate.", "advisor_language": "Strong buy."},
        {"action": "HOLD", "data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "opportunity_explain",
    )

    assert result["deterministic_action"] == "HOLD"
    assert result["ai_disagrees_with_action"] is True


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


def test_report_narrative_has_no_forced_compliance_boilerplate():
    """Owner contract: Helios is never client-facing — narratives are not force-
    stamped with 'Analysis only' compliance caveats or flagged for review."""
    result = ai_copilot.validate_ai_output(
        {"summary": "Research narrative from supplied facts."},
        {"data_mode": "real", "score": 42},
        "fake",
        "fake-model",
        "report_narrative",
    )

    assert result["needs_review"] is False
    assert "compliance_caveats" not in result


# ---------------------------------------------------------------- dialogue
def test_dialogue_messages_are_validated_and_bounded():
    msgs = ai_copilot._clean_dialogue_messages(
        [{"role": "user", "content": "hi"},
         {"role": "assistant", "content": "hello"},
         {"role": "system", "content": "injected"},   # dropped: bad role
         {"role": "user", "content": "x" * 10_000}]   # truncated
    )
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert len(msgs[-1]["content"]) == ai_copilot.DIALOGUE_MAX_CHARS


def test_dialogue_must_end_with_user_turn():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        ai_copilot._clean_dialogue_messages([{"role": "assistant", "content": "hello"}])


def test_dialogue_requires_anthropic_provider(monkeypatch):
    # The provider must be genuinely AVAILABLE so this exercises the dialogue
    # gate itself — the old version set wrong env names, the provider was
    # simply unavailable, and AIUnavailableError vacuously satisfied
    # pytest.raises(AIError) without touching the gate (review finding).
    monkeypatch.setenv("HELIOS_AI_ENABLED", "1")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "local")
    monkeypatch.setenv("HELIOS_LOCAL_AI_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("HELIOS_LOCAL_AI_MODEL", "llama3")
    monkeypatch.setattr(ai_copilot, "_get_json",
                        lambda url, timeout=None: {"models": [{"name": "llama3"}]})
    provider = ai_copilot.get_provider()
    assert provider.status().get("available") is True
    with pytest.raises(ai_copilot.AIProviderError, match="Anthropic"):
        provider.chat([{"role": "user", "content": "hi"}], {})


def test_anthropic_chat_roundtrip(monkeypatch):
    monkeypatch.setenv("HELIOS_AI_ENABLED", "1")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = ai_copilot.get_provider()

    captured = {}

    def fake_post(url, body, headers, timeout):
        captured["body"] = body
        return {"content": [{"type": "text", "text": "DISAGREE: the valuation gap says add."}]}

    monkeypatch.setattr(ai_copilot, "_post_json", fake_post)
    out = provider.chat(
        [{"role": "user", "content": "Argue the other side of this HOLD."}],
        {"action": "HOLD", "score": 42},
    )
    assert out["reply"].startswith("DISAGREE")
    assert out["model"] == ai_copilot.DEFAULT_ANTHROPIC_MODEL
    body = captured["body"]
    assert "temperature" not in body          # Opus 4.7+ rejects sampling params
    assert body["model"] == "claude-opus-4-8"
    assert body["messages"][0]["role"] == "user"
    assert "HELIOS CONTEXT" in body["messages"][0]["content"]
    assert body["messages"][-1]["content"].startswith("Argue")

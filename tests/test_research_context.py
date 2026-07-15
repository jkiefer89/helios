from __future__ import annotations

import numpy as np
import pandas as pd

import app as helios
from engine import data, evidence, persistence, research_context


def _use_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HELIOS_DB_PATH", str(tmp_path / "helios.db"))
    monkeypatch.setenv("HELIOS_DB_ENCRYPTION", "off")
    persistence.reset_store_for_tests()
    store = persistence.get_store()
    assert store.available is True
    return store


def _instrument(symbol: str = "SOXX") -> data.Instrument:
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=320)
    returns = np.resize(np.array([0.011, -0.005, 0.008, -0.003, 0.004]), len(idx))
    close = pd.Series(100.0 * np.cumprod(1.0 + returns), index=idx, name="close")
    inst = data.Instrument(symbol, "Semiconductor ETF", close.to_frame(), "upload", [])
    data.register(inst)
    return inst


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


def test_instrument_context_is_versioned_and_has_immutable_evidence(monkeypatch, tmp_path):
    store = _use_db(monkeypatch, tmp_path)
    inst = _instrument()

    first = research_context.save_instrument_context(
        inst,
        thesis="Semiconductor capacity and AI compute demand should compound faster than the benchmark.",
        mandate_key="pure_growth",
        benchmark="QQQ",
        horizon_days=63,
        invalidation_criteria=["Demand growth falls below the benchmark for two review periods."],
        change_note="Initial governed context.",
        actor="researcher@example.test",
    )
    second = research_context.save_instrument_context(
        inst,
        thesis="Semiconductor capacity and AI compute demand should compound with measured downside risk.",
        mandate_key="balanced",
        benchmark="SPY",
        horizon_days=42,
        invalidation_criteria=["Relative strength remains negative through two review periods."],
        change_note="Changed mandate after risk review.",
        actor="researcher@example.test",
    )

    assert first["version"] == 1
    assert second["version"] == 2
    assert research_context.instrument_context("SOXX")["benchmark"] == "SPY"
    history = store.research_context_events("instrument", "SOXX")
    assert [row["version"] for row in history] == [2, 1]
    assert history[0]["change_note"] == "Changed mandate after risk review."
    verified = evidence.verify(history[0]["evidence"]["evidence_id"])
    assert verified["verified"] is True


def test_research_context_endpoint_requires_complete_operator_context(monkeypatch, tmp_path):
    _use_db(monkeypatch, tmp_path)
    _instrument()
    client = _client()

    missing = client.post("/api/research-context", json={"target_id": "SOXX"})
    assert missing.status_code == 400
    assert "thesis" in missing.get_json()["error"].lower()

    saved = client.post("/api/research-context", json={
        "target_kind": "instrument",
        "target_id": "SOXX",
        "thesis": "Own semiconductor breadth only while demand and relative strength support the mandate.",
        "mandate_key": "pure_growth",
        "benchmark": "QQQ",
        "horizon_days": 63,
        "invalidation_criteria": ["Relative strength stays below QQQ for two reviews."],
        "change_note": "Set initial instrument thesis.",
    })
    assert saved.status_code == 200
    context = saved.get_json()["research_context"]
    assert context["configured"] is True
    assert context["mandate_label"] == "Pure Growth"
    assert context["benchmark"] == "QQQ"

    loaded = client.get("/api/research-context?target_id=SOXX")
    assert loaded.status_code == 200
    assert loaded.get_json()["research_context"]["version"] == 1


def test_research_context_save_fails_cleanly_without_persistence():
    inst = _instrument()

    try:
        research_context.save_instrument_context(
            inst,
            thesis="A complete thesis that should not be persisted when the database is disabled.",
            mandate_key="balanced",
            benchmark="SPY",
            horizon_days=21,
            invalidation_criteria=["The core thesis no longer holds."],
            change_note="Attempt without storage.",
            actor="tester",
        )
    except RuntimeError as exc:
        assert "persistence" in str(exc).lower()
    else:
        raise AssertionError("Saving governed context must fail closed without persistence.")

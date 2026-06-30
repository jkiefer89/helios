import app as helios
from engine import data, portfolio


def test_model_library_api_returns_governed_templates():
    client = helios.app.test_client()

    resp = client.get("/api/model-library")

    assert resp.status_code == 200
    templates = resp.get_json()["templates"]
    slugs = {template["slug"] for template in templates}
    assert slugs == {
        "ai-infrastructure",
        "quality-compounders",
        "defense-security",
        "energy-grid",
        "healthcare-innovation",
        "inflation-hedges",
        "cash-defensive",
    }
    for template in templates:
        assert template["template_only"] is True
        assert template["mandate"]
        assert template["benchmark"]
        assert template["rebalance_rules"]["frequency"]
        assert template["risk_limits"]["max_single_position_pct"] > 0
        assert template["provenance"]["source_type"] == "curated_template"
        assert "not investment advice" in template["provenance"]["caveat"].lower()
        assert round(sum(holding["weight"] for holding in template["holdings"]), 6) == 1.0


def test_model_library_import_creates_normal_model_without_network_or_real_promotion(monkeypatch):
    client = helios.app.test_client()
    monkeypatch.setattr(data, "HAS_YF", False)

    resp = client.post("/api/model-library/import", json={"slug": "cash-defensive"})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == "CASH-DEFENSIVE"
    assert body["name"] == "Cash and Defensive Reserve"
    assert body["mandate"] == "cd_alternative"
    assert body["n_holdings"] == 8
    assert body["template_only"] is True
    assert "not investment advice" in body["provenance"]["caveat"].lower()
    assert body["coverage_state"] in {"mixed", "blocked"}

    model = portfolio.get(body["id"])
    assert model is not None
    assert model.mandate_context
    assert "Benchmark:" in model.mandate_context
    assert "Rebalance:" in model.mandate_context
    assert "Risk limits:" in model.mandate_context


def test_starter_live_universe_covers_every_model_library_holding():
    client = helios.app.test_client()

    templates = client.get("/api/model-library").get_json()["templates"]
    library_symbols = {holding["ticker"] for template in templates for holding in template["holdings"]}
    starter_symbols = set(data.expand_live_symbols("starter_models"))

    assert library_symbols <= starter_symbols

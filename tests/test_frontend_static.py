from pathlib import Path

import app as helios


ROOT = Path(__file__).resolve().parents[1]


def test_return_metric_labels_are_explicit_about_mean_annualization():
    text = (ROOT / "static" / "app.js").read_text()

    assert text.count("Mean annual return") == 2
    assert '"Annual return"' not in text


def test_react_frontend_scaffold_exists():
    assert (ROOT / "frontend" / "package.json").is_file()
    assert (ROOT / "frontend" / "vite.config.ts").is_file()
    assert (ROOT / "frontend" / "src" / "App.tsx").is_file()
    assert (ROOT / "frontend" / "src" / "api" / "client.ts").is_file()


def test_react_terminal_exposes_richer_chart_components():
    chart_source = (ROOT / "frontend" / "src" / "components" / "charts" / "Charts.tsx").read_text()
    opportunity_source = (ROOT / "frontend" / "src" / "views" / "OpportunityRadar.tsx").read_text()
    clinic_source = (ROOT / "frontend" / "src" / "views" / "PortfolioClinic.tsx").read_text()
    strategy_source = (ROOT / "frontend" / "src" / "views" / "StrategyLab.tsx").read_text()

    assert "export function ScoreScatter" in chart_source
    assert "export function HistogramChart" in chart_source
    assert "export function DonutChart" in chart_source
    assert "ScoreScatter" in opportunity_source
    assert "HistogramChart" in opportunity_source
    assert "DonutChart" in clinic_source
    assert "ChartSummary" in strategy_source


def test_react_terminal_uses_live_aware_data_honesty_copy():
    command_source = (ROOT / "frontend" / "src" / "views" / "CommandCenter.tsx").read_text()
    opportunity_source = (ROOT / "frontend" / "src" / "views" / "OpportunityRadar.tsx").read_text()

    assert "<strong>Demo / gated data</strong>" not in command_source
    assert "<strong>Real data required</strong>" not in command_source
    assert "buildDisclosureCards(payload, sourceStatus)" in command_source
    assert "formatSourceStatus" in command_source
    assert "Real data active" in command_source
    assert "Research unlocked" in command_source
    assert "Loading real-data rankings" in opportunity_source
    assert "setIsLoading(true)" in opportunity_source


def test_ai_copilot_disabled_state_is_non_actionable_and_not_repetitive():
    source = (ROOT / "frontend" / "src" / "components" / "ai" / "AICopilotPanel.tsx").read_text()

    assert "const unavailableReason" in source
    assert "{unavailableReason && <span>{unavailableReason}</span>}" in source
    assert "disabled={Boolean(unavailableReason) || loading}" in source
    assert "AI Copilot unavailable" in source
    assert source.count("AI Copilot is off. Helios analytics still work normally.") == 1


def test_flask_serves_react_build_when_present(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('helios')", encoding="utf-8")
    monkeypatch.setattr(helios, "FRONTEND_DIST", dist)
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    root = client.get("/")
    asset = client.get("/assets/app.js")
    spa = client.get("/reports")

    assert root.status_code == 200
    assert b'id="root"' in root.data
    assert asset.status_code == 200
    assert asset.data == b"console.log('helios')"
    assert spa.status_code == 200
    assert b'id="root"' in spa.data


def test_flask_falls_back_to_legacy_when_react_build_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(helios, "FRONTEND_DIST", tmp_path / "missing-dist")
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    root = client.get("/")
    legacy = client.get("/legacy")
    api_miss = client.get("/api/nope")

    assert root.status_code == 200
    assert legacy.status_code == 200
    assert b"Helios" in root.data
    assert b"Helios" in legacy.data
    assert api_miss.status_code == 404
    assert api_miss.is_json

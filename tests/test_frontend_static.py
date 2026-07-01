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


def test_real_data_onboarding_copy_switches_when_live_histories_exist():
    source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()

    assert "const onboardingCopy" in source
    assert "Live refresh active" in source
    assert "ready for real research" in source
    assert "Sample data remains demo-only." not in source


def test_models_view_exposes_governed_model_library():
    source = (ROOT / "frontend" / "src" / "views" / "Models.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Model Library" in source
    assert "Risk limits" in source
    assert "Rebalance" in source
    assert "Import template" in source
    assert "Model Governance" in source
    assert "Approval Status" in source
    assert "Mandate / Risk Limits" in source
    assert "Archived Snapshots" in source
    assert "Rebalance History" in source
    assert "Who changed what" in source
    assert "api.modelGovernance" in source
    assert "modelGovernance:" in client_source
    assert "ModelGovernanceResponse" in type_source


def test_reports_view_exposes_signal_journal():
    source = (ROOT / "frontend" / "src" / "views" / "Reports.tsx").read_text()

    assert "Signal Journal" in source
    assert "api.signalJournal" in source
    assert "Forward Result" in source
    assert "Paper tracking only" in source


def test_signal_journal_has_dedicated_workspace():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()
    view_source = (ROOT / "frontend" / "src" / "views" / "SignalJournal.tsx").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert '"journal"' in shell_source
    assert "Signal Journal" in shell_source
    assert "SignalJournal" in app_source
    assert "api.signalJournal" in view_source
    assert "Paper performance tracking" in view_source
    assert "Hit Rate" in view_source
    assert "Pending Forward Results" in view_source
    assert "Benchmark Comparison" in view_source
    assert "Model-by-Model Evidence" in view_source
    assert "Drift Over Time" in view_source
    assert "LineChart" in view_source
    assert "SignalJournalSummary" in type_source


def test_reports_view_exposes_report_snapshot_exports():
    source = (ROOT / "frontend" / "src" / "views" / "Reports.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Report History" in source
    assert "Save snapshot" in source
    assert "HTML Snapshot" in source
    assert "PDF Export" in source
    assert "Include AI narrative when available" in source
    assert "Encrypted local history" in source
    assert "api.saveReportSnapshot" in source
    assert "reportSnapshots" in client_source
    assert "ReportSnapshot" in type_source


def test_data_quality_dashboard_view_is_dedicated_and_routed():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()
    view_source = (ROOT / "frontend" / "src" / "views" / "DataQuality.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert '"data-quality"' in shell_source
    assert "Data Quality" in shell_source
    assert "DataQuality" in app_source
    assert "api.dataQuality" in view_source
    assert "dataQuality:" in client_source
    assert "DataQualityResponse" in type_source
    assert "Institutional Data Quality" in view_source
    assert "Stale Symbols" in view_source
    assert "Short Histories" in view_source
    assert "Refresh Failures" in view_source
    assert "Refresh Evidence" in view_source
    assert "Observability Gaps" in view_source
    assert "Coverage Gaps" in view_source
    assert "Research-ready" in view_source


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

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
    assert "Model Governance v2" in source
    assert "Reject" in source
    assert "Approval Packet" in source
    assert "Version Diff" in source
    assert "Risk-limit blocked" in source
    assert "api.modelApprovalPacket" in source
    assert "modelApprovalPacket:" in client_source
    assert "api.modelGovernance" in source
    assert "modelGovernance:" in client_source
    assert "ModelGovernanceResponse" in type_source
    assert "ModelGovernanceApprovalPacket" in type_source


def test_models_view_exposes_native_model_editor():
    source = (ROOT / "frontend" / "src" / "views" / "Models.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Model Editor" in source
    assert "Change holdings and target weights" in source
    assert "Rebalance to target" in source
    assert "Preview breaches" in source
    assert "Change note" in source
    assert "api.previewModelEdit" in source
    assert "api.saveModelEdit" in source
    assert "previewModelEdit:" in client_source
    assert "saveModelEdit:" in client_source
    assert "ModelEditPreviewResponse" in type_source
    assert "ModelEditSaveResponse" in type_source


def test_models_view_exposes_model_validation_dashboard():
    source = (ROOT / "frontend" / "src" / "views" / "Models.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Model Validation Dashboard" in source
    assert "Champion / Challenger" in source
    assert "Walk-Forward Evidence" in source
    assert "False Positives" in source
    assert "Regime Sensitivity" in source
    assert "Signal Decay" in source
    assert "Drift Alerts" in source
    assert "api.modelValidation" in source
    assert "modelValidation:" in client_source
    assert "ModelValidationResponse" in type_source


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


def test_reports_view_exposes_institutional_report_system():
    source = (ROOT / "frontend" / "src" / "views" / "Reports.tsx").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Institutional Report System" in source
    assert "Advisor/client-ready reports" in source
    assert "Prepared for" in source
    assert "Prepared by" in source
    assert "Reviewer" in source
    assert "Report Version" in source
    assert "Audit Trail" in source
    assert "Disclosure Blocks" in source
    assert "Print / PDF layout" in source
    assert "version_label" in source
    assert "audit_trail" in type_source
    assert "disclosure_blocks" in type_source
    assert "output_formats" in type_source


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
    assert "Alert Center" in view_source
    assert "Active Alerts" in view_source
    assert "Resolved Alerts" in view_source
    assert "notification_state" in type_source
    assert "DataQualityAlert" in type_source


def test_risk_analytics_view_is_dedicated_and_routed():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()
    view_source = (ROOT / "frontend" / "src" / "views" / "RiskAnalytics.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert '"risk"' in shell_source
    assert "Risk Analytics" in shell_source
    assert "RiskAnalytics" in app_source
    assert "api.modelRisk" in view_source
    assert "modelRisk:" in client_source
    assert "RiskAnalyticsResponse" in type_source
    assert "Factor Exposure" in view_source
    assert "Sector / Theme Exposure" in view_source
    assert "Correlation Clusters" in view_source
    assert "Scenario Shocks" in view_source
    assert "Liquidity Flags" in view_source
    assert "Benchmark-Relative Risk" in view_source


def test_risk_analytics_view_exposes_client_grade_risk_pack():
    view_source = (ROOT / "frontend" / "src" / "views" / "RiskAnalytics.tsx").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Client-Grade Risk Pack" in view_source
    assert "What Would Break This Model" in view_source
    assert "Benchmark-Relative Drawdown" in view_source
    assert "Concentration Warnings" in view_source
    assert "Liquidity Watchlist" in view_source
    assert "Stress Scenarios" in view_source
    assert "Historical Stress Replay" in view_source
    assert "Observed ADV" in view_source
    assert "Breakpoint Evidence" in view_source
    assert "client_risk_pack" in view_source
    assert "ClientRiskPack" in type_source
    assert "historical_stress_replay" in type_source
    assert "observed_adv_usd" in type_source


def test_evidence_lab_view_is_dedicated_and_routed():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()
    view_source = (ROOT / "frontend" / "src" / "views" / "EvidenceLab.tsx").read_text()
    client_source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert '"evidence"' in shell_source
    assert "Evidence Lab" in shell_source
    assert "EvidenceLab" in app_source
    assert "api.evidenceLab" in view_source
    assert "evidenceLab:" in client_source
    assert "EvidenceLabResponse" in type_source
    assert "Walk-forward evidence" in view_source
    assert "Hit Rate" in view_source
    assert "Alpha vs Benchmark" in view_source
    assert "False Positives" in view_source
    assert "Regime Sensitivity" in view_source
    assert "Signal Decay" in view_source
    assert "Confidence Bands" in view_source
    assert "Prospective Validation" in view_source
    assert "Signal Journal" in view_source


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

from pathlib import Path

import app as helios
from helios_web import core as web_core


ROOT = Path(__file__).resolve().parents[1]


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


def test_react_terminal_uses_helios_echarts_foundation():
    package_json = (ROOT / "frontend" / "package.json").read_text()
    package_lock = (ROOT / "frontend" / "package-lock.json").read_text()
    vite_config = (ROOT / "frontend" / "vite.config.ts").read_text()
    chart_source = (ROOT / "frontend" / "src" / "components" / "charts" / "Charts.tsx").read_text()
    wrapper_source = (ROOT / "frontend" / "src" / "components" / "charts" / "HeliosEChart.tsx").read_text()
    theme_source = (ROOT / "frontend" / "src" / "components" / "charts" / "chartTheme.ts").read_text()
    states_source = (ROOT / "frontend" / "src" / "components" / "charts" / "chartStates.tsx").read_text()
    equity_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "equity.ts").read_text()
    drawdown_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "drawdown.ts").read_text()
    sharpe_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "rollingSharpe.ts").read_text()
    price_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "priceTrend.ts").read_text()
    forecast_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "forecastCone.ts").read_text()
    analysis_source = (ROOT / "frontend" / "src" / "views" / "Analysis.tsx").read_text()
    strategy_source = (ROOT / "frontend" / "src" / "views" / "StrategyLab.tsx").read_text()

    assert '"echarts"' in package_json
    # echarts-for-react is intentionally absent: the CJS wrapper broke under
    # Vite 8's ESM interop (React error #130); HeliosEChart drives echarts/core.
    assert '"echarts-for-react"' not in package_json
    assert '"node_modules/echarts"' in package_lock
    assert '"node_modules/echarts-for-react"' not in package_lock
    assert "chunkSizeWarningLimit" in vite_config
    assert "echarts.init" in wrapper_source
    assert "chart.dispose()" in wrapper_source
    assert "ResizeObserver" in wrapper_source
    assert "lazy(() => import(\"./HeliosEChart\")" in chart_source
    assert "<Suspense" in chart_source
    assert 'renderer = "svg"' in wrapper_source
    assert "{ renderer }" in wrapper_source
    assert "HELIOS_CHART_THEME" in theme_source
    assert "HELIOS_CHART_FORMATTERS" in theme_source
    assert "axisTooltipFormatter" in theme_source
    assert "chartAlpha" in theme_source
    assert "ChartState" in states_source
    assert "LoadingChartState" in states_source
    # Dead chart-state wrapper exports were removed; kinds stay on ChartState.
    assert "ErrorChartState" not in states_source
    assert "LockedChartState" not in states_source
    assert '"empty" | "loading" | "error" | "locked"' in states_source
    assert "minHeight" in states_source
    assert "equityCurveOption" in equity_adapter
    assert "drawdownOption" in drawdown_adapter
    assert "rollingSharpeOption" in sharpe_adapter
    assert "priceTrendOption" in price_adapter
    assert "forecastConeOption" in forecast_adapter
    assert "ForecastConePoint" in forecast_adapter
    assert "EquityCurveChart" in chart_source
    assert "DrawdownChart" in chart_source
    assert "RollingSharpeChart" in chart_source
    assert "PriceTrendChart" in chart_source
    assert "LoadingChartState" in chart_source
    assert "minHeight={height}" in chart_source
    assert "points.map((point) => point.close)" in chart_source
    assert "points.map((point) => point.strategy)" in chart_source
    assert "rgba(" not in equity_adapter
    assert "rgba(" not in drawdown_adapter
    assert "rgba(" not in forecast_adapter
    assert "\"{value}\"" not in equity_adapter
    assert "\"{value}\"" not in drawdown_adapter
    assert "\"{value}\"" not in sharpe_adapter
    assert "\"{value}\"" not in price_adapter
    assert "\"{value}\"" not in forecast_adapter
    assert "PriceTrendChart" in analysis_source
    assert "EquityCurveChart" in strategy_source
    assert "RollingSharpeChart" in strategy_source


def test_react_analysis_view_reaches_legacy_parity():
    analysis_source = (ROOT / "frontend" / "src" / "views" / "Analysis.tsx").read_text()
    chart_source = (ROOT / "frontend" / "src" / "components" / "charts" / "Charts.tsx").read_text()
    wrapper_source = (ROOT / "frontend" / "src" / "components" / "charts" / "HeliosEChart.tsx").read_text()
    price_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "priceTrend.ts").read_text()
    forecast_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "forecastCone.ts").read_text()
    rsi_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "rsi.ts").read_text()
    macd_adapter = (ROOT / "frontend" / "src" / "components" / "charts" / "adapters" / "macd.ts").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    # The placeholder key/value dumps and stale header copy are gone.
    assert "Legacy analytics payloads rendered in the React terminal" not in analysis_source
    assert "KeyObject" not in analysis_source

    # Forecast cone: tactical horizon plus 6M/1Y/3Y/5Y strategic presets.
    assert "ForecastConeChart" in chart_source
    assert "forecastConeOption" in chart_source
    assert "ForecastConeChart" in analysis_source
    assert 'LONG_HORIZON_PRESETS = ["6M", "1Y", "3Y", "5Y"]' in analysis_source
    assert "available_long" in analysis_source
    assert "Strategic Value Projection ($10,000 base)" in analysis_source
    assert "drawdown tolerance" in analysis_source
    assert "prob_breach_maxdd" in analysis_source
    assert "p05" in forecast_adapter and "p95" in forecast_adapter
    assert "rgba(" not in rsi_adapter
    assert "rgba(" not in macd_adapter

    # Indicator panels and price-chart overlays.
    assert "RsiChart" in chart_source and "MacdChart" in chart_source
    assert "rsiOption" in rsi_adapter and "macdOption" in macd_adapter
    assert "RsiChart" in analysis_source and "MacdChart" in analysis_source
    assert "bbUpper" in price_adapter and "PriceTrendMarker" in price_adapter
    assert "bb_upper" in analysis_source and "markers" in analysis_source
    assert "EChartsBarChart" in wrapper_source and "EChartsScatterChart" in wrapper_source

    # Designed evidence panels replacing the raw dumps.
    assert "Signal Component Breakdown" in analysis_source
    assert "weight-chip" in analysis_source
    assert "clause-list" in analysis_source
    assert "News Sentiment" in analysis_source
    assert "Mandate Fit" in analysis_source
    assert "Model Insights" in analysis_source
    assert "suggested_action" in analysis_source
    assert "EquityCurveChart" in analysis_source

    # The API types now expose the full backend analysis contract.
    assert "AnalysisSeries" in type_source
    assert "TacticalForecast" in type_source
    assert "LongForecast" in type_source
    assert "SignalComponent" in type_source
    assert "SentimentPayload" in type_source
    assert "ModelInsight" in type_source
    assert "macd_hist" in type_source
    assert "SignalMarker" in type_source


def test_analysis_view_prefers_engine_data_provenance_verdict():
    analysis_source = (ROOT / "frontend" / "src" / "views" / "Analysis.tsx").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    # The engine's provenance verdict is consumed verbatim; the client-side
    # gate derivation survives only as a fallback for older cached responses.
    assert "const verdict = payload.data_provenance" in analysis_source
    assert "verdict.eligible_for_real_research === true" in analysis_source
    assert "data_provenance: verdict" in analysis_source
    assert "data_provenance?: DataQuality" in type_source

    # Mandate-fit pass/fail mirrors engine insight verdicts instead of
    # re-implemented client-side methodology thresholds.
    assert "targetVol * 1.15" not in analysis_source
    assert '"vol_above_mandate"' in analysis_source
    assert '"drawdown_breaches_tolerance"' in analysis_source
    assert "illustrative fit indication" in analysis_source


def test_opportunity_radar_renders_blocked_models_panel():
    opportunity_source = (ROOT / "frontend" / "src" / "views" / "OpportunityRadar.tsx").read_text()
    type_source = (ROOT / "frontend" / "src" / "api" / "types.ts").read_text()

    assert "Blocked Models" in opportunity_source
    assert "BlockedModelsTable" in opportunity_source
    assert "payload?.blocked_items" in opportunity_source
    assert "missing_tickers" in opportunity_source
    assert "required_action" in opportunity_source
    # Reuses the existing locked-state styling; no new design surface.
    assert 'className="locked-table-row"' in opportunity_source
    assert "BlockedOpportunityItem" in type_source
    assert "blocked_items?: BlockedOpportunityItem[]" in type_source


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
    assert "useViewFetch" in opportunity_source
    assert "isLoading" in opportunity_source


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
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
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
    assert "Export PDF" in source
    assert "Committee Identity" in source
    assert "Local approval PIN" in source
    assert "Version Diff" in source
    assert "Risk-limit blocked" in source
    assert "api.modelApprovalPacket" in source
    assert "modelApprovalPacket:" in client_source
    assert "api.modelGovernance" in app_source
    assert "modelGovernance:" in client_source
    assert "ModelGovernanceResponse" in type_source
    assert "ModelGovernanceApprovalPacket" in type_source
    assert "committee_identity" in type_source
    assert "pdf_url" in type_source


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


def test_payload_views_share_the_view_fetch_hook():
    hook_source = (ROOT / "frontend" / "src" / "hooks" / "useViewFetch.ts").read_text()
    views_dir = ROOT / "frontend" / "src" / "views"

    assert "requestSeq" in hook_source
    assert "isCurrentTarget" in hook_source
    assert "keepPayloadWhileLoading" in hook_source
    for view in ["Analysis", "StrategyLab", "Reports", "PortfolioClinic", "RiskAnalytics", "EvidenceLab", "OpportunityRadar"]:
        source = (views_dir / f"{view}.tsx").read_text()
        assert "useViewFetch" in source, view
        assert "isLoading" in source, view
        assert "requestSeq" not in source, view


def test_payload_views_guard_against_selection_sync_double_fetch():
    views_dir = ROOT / "frontend" / "src" / "views"
    for view, target in [
        ("Analysis", "defaultTarget"),
        ("StrategyLab", "defaultTarget"),
        ("Reports", "defaultTarget"),
        ("PortfolioClinic", "defaultModelId"),
        ("RiskAnalytics", "defaultModelId"),
        ("EvidenceLab", "defaultTarget"),
    ]:
        source = (views_dir / f"{view}.tsx").read_text()
        assert f"isCurrentTarget({target})" in source, view


def test_react_app_is_wrapped_in_an_error_boundary():
    main_source = (ROOT / "frontend" / "src" / "main.tsx").read_text()
    boundary_source = (ROOT / "frontend" / "src" / "components" / "layout" / "ErrorBoundary.tsx").read_text()

    assert "<ErrorBoundary>" in main_source
    assert "getDerivedStateFromError" in boundary_source
    assert "window.location.reload()" in boundary_source


def test_react_app_syncs_view_and_selection_to_location_hash():
    app_source = (ROOT / "frontend" / "src" / "App.tsx").read_text()
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()

    assert "parseHash" in app_source
    assert "buildHash" in app_source
    assert 'window.addEventListener("hashchange"' in app_source
    assert "isViewId" in shell_source


def test_react_terminal_exposes_accessible_status_and_search_semantics():
    shell_source = (ROOT / "frontend" / "src" / "components" / "layout" / "AppShell.tsx").read_text()
    views_dir = ROOT / "frontend" / "src" / "views"

    assert '<div className="notice" role="status" aria-live="polite">' in shell_source
    assert 'role="combobox"' in shell_source
    assert 'aria-autocomplete="list"' in shell_source
    assert "aria-activedescendant=" in shell_source
    assert 'role="listbox"' in shell_source
    assert 'role="option"' in shell_source
    for view in sorted(views_dir.glob("*.tsx")):
        source = view.read_text()
        assert '<div className="notice danger">' not in source, view.name


def test_frontend_eslint_is_configured():
    package_json = (ROOT / "frontend" / "package.json").read_text()
    package_lock = (ROOT / "frontend" / "package-lock.json").read_text()
    eslint_config = (ROOT / "frontend" / "eslint.config.js").read_text()

    assert '"lint": "eslint ."' in package_json
    assert '"node_modules/eslint"' in package_lock
    assert "typescript-eslint" in eslint_config
    assert "react-hooks/exhaustive-deps" in eslint_config


def test_flask_serves_react_build_when_present(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('helios')", encoding="utf-8")
    monkeypatch.setattr(web_core, "FRONTEND_DIST", dist)
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


def test_legacy_dashboard_route_is_retired(tmp_path, monkeypatch):
    monkeypatch.setattr(web_core, "FRONTEND_DIST", tmp_path / "missing-dist")
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    legacy = client.get("/legacy")

    assert legacy.status_code == 404
    assert legacy.is_json


def test_flask_serves_build_instructions_when_react_build_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(web_core, "FRONTEND_DIST", tmp_path / "missing-dist")
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    root = client.get("/")
    api_miss = client.get("/api/nope")

    assert root.status_code == 200
    assert b"Helios" in root.data
    assert b"npm --prefix frontend ci" in root.data
    assert b"npm --prefix frontend run build" in root.data
    # Self-contained: no external scripts, stylesheets, or images.
    assert b"http://" not in root.data
    assert b"https://" not in root.data
    assert b"<script" not in root.data
    assert root.headers["Content-Security-Policy"].count("script-src 'self';") == 1
    assert api_miss.status_code == 404
    assert api_miss.is_json


def test_csp_script_src_is_self_with_no_cdn_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(web_core, "FRONTEND_DIST", tmp_path / "missing-dist")
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    for path in ("/", "/api/tickers", "/api/nope"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "jsdelivr" not in csp, path
        assert "script-src 'self';" in csp, path

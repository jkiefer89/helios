import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ModelSummary, RiskAnalyticsResponse } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { DonutChart, MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtMoney, fmtNumber, fmtPct, titleCase } from "../utils/format";

export function RiskAnalytics({
  models,
  selectedModel,
  onSelectModel,
}: {
  models: ModelSummary[];
  selectedModel?: string;
  onSelectModel: (id: string) => void;
}) {
  const defaultModelId = selectedModel || models[0]?.id || "";
  const [modelId, setModelId] = useState(defaultModelId);
  const [payload, setPayload] = useState<RiskAnalyticsResponse | null>(null);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);
  const modelOptions = models.length > 0
    ? models.map((model) => ({ value: model.id, label: `${model.name} · ${model.mandate_label}` }))
    : [{ value: "", label: "No models imported" }];
  const factorRows = useMemo(() => {
    return Object.entries(payload?.factor_exposure || {})
      .map(([label, value]) => ({ label: titleCase(label), value, tone: value >= 45 ? "warning" : "info" }))
      .sort((left, right) => right.value - left.value);
  }, [payload]);

  const run = async (requestedModelId = modelId) => {
    if (!requestedModelId) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      onSelectModel(requestedModelId);
      const result = await api.modelRisk(requestedModelId);
      if (requestId !== requestSeq.current) return;
      setPayload(result);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Risk analytics failed.");
    }
  };

  useEffect(() => {
    if (!defaultModelId) return;
    setModelId(defaultModelId);
    void run(defaultModelId);
  }, [defaultModelId]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div>
          <div className="section-label">Risk Analytics</div>
          <h1>Risk + portfolio analytics</h1>
          <p>Portfolio-level factor exposure, concentration, stress, liquidity, and benchmark-relative risk from real model histories.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void run(); }}>
          <label>Model<TerminalSelect ariaLabel="Risk Analytics model" value={modelId} onChange={setModelId} options={modelOptions} disabled={models.length === 0} /></label>
          <button type="submit" disabled={models.length === 0}>Analyze risk</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {models.length === 0 && (
        <Panel title="Risk Analytics Gate" meta="model required">
          <EmptyState title="No model imported" body="Import or upload a model before running portfolio-level risk analytics." />
        </Panel>
      )}
      {payload && <DataQualityBanner payload={payload} />}
      {payload?.risk_exposure_unavailable && (
        <Panel title="Risk Analytics Locked" meta="real data required">
          <EmptyState title="Real price history required" body={payload.required_action || payload.reason || "Fetch or upload real histories for every holding before running portfolio risk analytics."} />
        </Panel>
      )}
      {payload && !payload.risk_exposure_unavailable && (
        <>
          <section className="dashboard-grid three">
            <Panel title="Concentration" meta={payload.single_name_concentration.status || "portfolio"}>
              <div className="metric-grid">
                <StatTile label="HHI" value={fmtNumber(payload.single_name_concentration.hhi, 3)} />
                <StatTile label="Effective holdings" value={fmtNumber(payload.single_name_concentration.effective_holdings, 1)} />
                <StatTile label="Top holding" value={`${payload.single_name_concentration.top_holding?.ticker || "—"} ${fmtPct(payload.single_name_concentration.top_holding?.weight_pct)}`} />
                <StatTile label="Top 5 weight" value={fmtPct(payload.single_name_concentration.top_5_weight_pct)} />
              </div>
            </Panel>
            <Panel title="Volatility Budget" meta={payload.volatility_budget.status === "over_budget" ? "over budget" : "within budget"}>
              <div className="metric-grid">
                <StatTile label="Annual vol" value={fmtPct(payload.volatility_budget.annual_vol_pct)} tone={payload.volatility_budget.status === "over_budget" ? "warning" : "positive"} />
                <StatTile label="Target vol" value={fmtPct(payload.volatility_budget.target_vol_pct)} />
                <StatTile label="Gap" value={fmtPct(payload.volatility_budget.gap_pct)} tone={(payload.volatility_budget.gap_pct || 0) > 0 ? "warning" : "positive"} />
                <StatTile label="Mandate" value={payload.mandate.label} />
              </div>
            </Panel>
            <Panel title="Benchmark-Relative Risk" meta={payload.benchmark_relative.benchmark_symbol || payload.benchmark?.symbol || "benchmark"}>
              <div className="metric-grid">
                <StatTile label="Beta" value={fmtNumber(payload.benchmark_relative.beta, 2)} />
                <StatTile label="Correlation" value={fmtNumber(payload.benchmark_relative.correlation, 2)} />
                <StatTile label="Active vol" value={fmtPct(payload.benchmark_relative.active_vol_pct)} />
                <StatTile label="Rel. drawdown" value={fmtPct(payload.benchmark_relative.relative_drawdown_pct)} tone="warning" />
              </div>
            </Panel>
          </section>

          <section className="dashboard-grid">
            <Panel title="Factor Exposure" meta="weighted taxonomy">
              <MiniBars rows={factorRows} />
            </Panel>
            <Panel title="Sector / Theme Exposure" meta="model weights">
              <div className="risk-exposure-pair">
                <DonutChart
                  segments={payload.sector_exposure.map((row) => ({ label: row.name, value: row.weight_pct, tone: row.weight_pct >= 35 ? "warning" : "info" }))}
                  centerLabel="Sector"
                />
                <MiniBars rows={payload.theme_exposure.slice(0, 8).map((row) => ({ label: row.name, value: row.weight_pct, tone: row.weight_pct >= 35 ? "warning" : "neutral" }))} />
              </div>
            </Panel>
          </section>

          <section className="dashboard-grid">
            <Panel title="Volatility Contribution" meta="marginal risk contribution">
              <MiniBars rows={payload.volatility_contribution.slice(0, 10).map((row) => ({ label: row.ticker, value: row.mrc_pct, tone: row.mrc_pct >= 25 ? "warning" : "info" }))} />
            </Panel>
            <Panel title="Drawdown Stress" meta="historical windows">
              <div className="metric-grid">
                <StatTile label="Max drawdown" value={fmtPct(payload.drawdown_stress.max_drawdown_pct)} tone="negative" />
                <StatTile label="Worst 21d" value={fmtPct(payload.drawdown_stress.worst_21d_pct)} tone="warning" />
                <StatTile label="Worst 63d" value={fmtPct(payload.drawdown_stress.worst_63d_pct)} tone="warning" />
                <StatTile label="Current DD" value={fmtPct(payload.drawdown_stress.current_drawdown_pct)} />
              </div>
            </Panel>
          </section>

          <section className="dashboard-grid">
            <Panel title="Scenario Shocks" meta="deterministic/not forecasts">
              <div className="terminal-table risk-table" tabIndex={0} aria-label="Scenario shock table">
                <div className="terminal-table__head"><span>Scenario</span><span>Impact</span><span>Basis</span></div>
                {payload.scenario_shocks.map((scenario) => (
                  <div className="table-row" key={scenario.scenario}>
                    <strong>{scenario.scenario}</strong>
                    <span className={scenario.portfolio_impact_pct < 0 ? "tone-negative" : "tone-positive"}>{fmtPct(scenario.portfolio_impact_pct)}</span>
                    <span>{scenario.basis}</span>
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="Correlation Clusters" meta="pairwise returns">
              {payload.correlation_clusters.length === 0 ? (
                <EmptyState title="No clusters" body="Correlation clusters appear when enough overlapping holding histories are available." />
              ) : (
                <div className="risk-cluster-list">
                  {payload.correlation_clusters.map((cluster) => (
                    <article key={cluster.name}>
                      <strong>{cluster.name}</strong>
                      <small>{cluster.average_correlation != null ? `Average ${fmtNumber(cluster.average_correlation, 2)}` : titleCase(cluster.type)}</small>
                      {cluster.pairs.slice(0, 5).map((pair) => (
                        <span key={pair.tickers.join("-")}>{pair.tickers.join(" / ")} <b>{fmtNumber(pair.correlation, 2)}</b></span>
                      ))}
                    </article>
                  ))}
                </div>
              )}
            </Panel>
          </section>

          <Panel title="Liquidity Flags" meta={`${payload.liquidity_flags.summary.flagged_count} flagged`}>
            <div className="terminal-table liquidity-table" tabIndex={0} aria-label="Liquidity flags table">
              <div className="terminal-table__head"><span>Ticker</span><span>Weight</span><span>Score</span><span>Flag</span><span>ADV proxy</span></div>
              {payload.liquidity_flags.items.map((item) => (
                <div className="table-row" key={item.ticker}>
                  <strong>{item.ticker}</strong>
                  <span>{fmtPct(item.weight_pct)}</span>
                  <span>{fmtNumber(item.liquidity_score, 0)}</span>
                  <span className={item.flag === "normal" ? "tone-positive" : "tone-warning"}>{titleCase(item.flag)}</span>
                  <span>{item.estimated_adv_usd ? fmtMoney(item.estimated_adv_usd) : "Verify with provider"}</span>
                </div>
              ))}
            </div>
            <p className="muted">{payload.liquidity_flags.summary.basis}</p>
          </Panel>
        </>
      )}
    </div>
  );
}

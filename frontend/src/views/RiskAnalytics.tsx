import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ClientRiskPack, ModelSummary, RiskAnalyticsResponse } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { DonutChart, MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
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
  // No first-model fallback: risk analytics run on the model the operator chose.
  const defaultModelId = selectedModel || "";
  const [modelId, setModelId] = useState(defaultModelId);
  const [aumInput, setAumInput] = useState("");
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<RiskAnalyticsResponse>({ failureMessage: "Risk analytics failed." });
  const modelOptions = models.length > 0
    ? models.map((model) => ({ value: model.id, label: `${model.name} · ${model.mandate_label}` }))
    : [{ value: "", label: "No models imported" }];
  const factorRows = useMemo(() => {
    return Object.entries(payload?.factor_exposure || {})
      .map(([label, value]) => ({ label: titleCase(label), value, tone: value >= 45 ? "warning" : "info" }))
      .sort((left, right) => right.value - left.value);
  }, [payload]);

  const run = useCallback((requestedModelId: string, aumRaw?: string) => {
    if (!requestedModelId) return;
    onSelectModel(requestedModelId);
    const parsed = Number((aumRaw ?? "").replace(/[$,\s]/g, ""));
    const aumUsd = Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
    void load(requestedModelId, () => api.modelRisk(requestedModelId, aumUsd));
  }, [load, onSelectModel]);

  useEffect(() => {
    if (!defaultModelId || isCurrentTarget(defaultModelId)) return;
    setModelId(defaultModelId);
    run(defaultModelId);
  }, [defaultModelId, isCurrentTarget, run]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div>
          <div className="section-label">Risk Analytics</div>
          <h1>Risk + portfolio analytics</h1>
          <p>Concentration, stress, liquidity, and benchmark-relative risk from real model histories; factor and sector exposure use a static hand-assigned taxonomy, not history.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); run(modelId, aumInput); }}>
          <label>Model<TerminalSelect ariaLabel="Risk Analytics model" value={modelId} onChange={setModelId} options={modelOptions} disabled={models.length === 0} /></label>
          <label>AUM $<input value={aumInput} onChange={(e) => setAumInput(e.target.value)}
            placeholder="e.g. 250,000,000" inputMode="numeric" aria-label="Model AUM in dollars" /></label>
          <button type="submit" disabled={models.length === 0}>Analyze risk</button>
        </form>
      </header>
      {error && <div className="notice danger" role="alert">{error}</div>}
      {isLoading && <div className="loading" role="status">Loading portfolio-level risk analytics...</div>}
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

          <ClientGradeRiskPack pack={payload.client_risk_pack} />

          <section className="dashboard-grid">
            <Panel title="Factor Exposure" meta="static sector heuristic — not regression betas">
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
              <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable scenario shocks">
                <table className="terminal-data-table scenario-shock-data-table">
                  <thead><tr><th scope="col">Scenario</th><th scope="col">Impact</th><th scope="col">Basis</th></tr></thead>
                  <tbody>{payload.scenario_shocks.map((scenario) => (
                  <tr key={scenario.scenario}>
                    <th scope="row">{scenario.scenario}</th>
                    <td className={scenario.portfolio_impact_pct < 0 ? "tone-negative" : "tone-positive"}>{fmtPct(scenario.portfolio_impact_pct)}</td>
                    <td>{scenario.basis}</td>
                  </tr>
                ))}</tbody>
                </table>
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
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable liquidity flags">
              <table className="terminal-data-table liquidity-data-table">
                <thead><tr><th scope="col">Ticker</th><th scope="col">Weight</th><th scope="col">Score</th><th scope="col">Flag</th><th scope="col">ADV proxy</th></tr></thead>
                <tbody>{payload.liquidity_flags.items.map((item) => (
                <tr key={item.ticker}>
                  <th scope="row">{item.ticker}</th>
                  <td>{fmtPct(item.weight_pct)}</td>
                  <td>{fmtNumber(item.liquidity_score, 0)}</td>
                  <td className={item.flag === "normal" ? "tone-positive" : "tone-warning"}>{titleCase(item.flag)}</td>
                  <td>{item.estimated_adv_usd ? fmtMoney(item.estimated_adv_usd) : "Verify with provider"}</td>
                </tr>
              ))}</tbody>
              </table>
            </div>
            <p className="muted">{payload.liquidity_flags.summary.basis}</p>
          </Panel>

          <CapacityPanel capacity={payload.capacity} />
        </>
      )}
    </div>
  );
}

function CapacityPanel({ capacity }: { capacity?: RiskAnalyticsResponse["capacity"] }) {
  if (!capacity) return null;
  if (capacity.status === "aum_not_set") {
    return (
      <Panel title="Implementation Capacity" meta="AUM required">
        <EmptyState title="No AUM set" body={capacity.note || "Enter the model's AUM above to size positions against real liquidity."} />
      </Panel>
    );
  }
  const tone = capacity.status === "capacity_constrained" ? "tone-negative"
    : capacity.status === "watch" ? "tone-warning" : "tone-positive";
  const rows = (capacity.holdings || []).filter((row) => row.status === "ok");
  const unsized = (capacity.holdings || []).filter((row) => row.status !== "ok");
  return (
    <Panel
      title="Implementation Capacity"
      meta={`AUM ${fmtMoney(capacity.aum_usd || 0)} · days-to-liquidate at 10%/20% ADV caps`}
    >
      <div className="metric-grid compact-metrics">
        <div className="stat-tile"><span>Status</span><strong className={tone}>{titleCase(String(capacity.status || ""))}</strong><small>{(capacity.holdings_over_20d || []).length ? `${(capacity.holdings_over_20d || []).join(", ")} >20d` : (capacity.holdings_over_5d || []).length ? `${(capacity.holdings_over_5d || []).join(", ")} >5d` : "All holdings exit ≤5 days at 10% cap"}</small></div>
        <div className="stat-tile"><span>Slowest exit</span><strong>{fmtNumber(capacity.max_days_to_liquidate_10pct, 1)}d</strong><small>at 10% participation</small></div>
        <div className="stat-tile"><span>Weighted exit</span><strong>{fmtNumber(capacity.weighted_days_to_liquidate_10pct, 1)}d</strong><small>weight-averaged</small></div>
        <div className="stat-tile"><span>Unverified ADV</span><strong>{fmtNumber((capacity.proxy_based_count || 0) + unsized.length, 0)}</strong><small>{capacity.proxy_based_count || 0} static proxy · {unsized.length} unsized</small></div>
      </div>
      <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable implementation capacity">
        <table className="terminal-data-table capacity-data-table">
          <thead><tr><th scope="col">Ticker</th><th scope="col">Position $</th><th scope="col">1-day part.</th><th scope="col">Days @10%</th><th scope="col">Est. impact</th></tr></thead>
          <tbody>{rows.map((row) => (
          <tr key={row.ticker}>
            <th scope="row">{row.ticker}<small>{row.adv_source === "static_public_proxy" ? "proxy ADV" : row.adv_source === "observed_60d_dollar_volume" ? "observed ADV" : row.adv_source}</small></th>
            <td>{fmtMoney(row.position_usd || 0)}</td>
            <td>{fmtPct(row.one_day_participation_pct)}</td>
            <td className={Number(row.days_to_liquidate_10pct) > 20 ? "tone-negative" : Number(row.days_to_liquidate_10pct) > 5 ? "tone-warning" : ""}>{fmtNumber(row.days_to_liquidate_10pct, 1)}d</td>
            <td>{row.impact_estimate_bps != null ? `${fmtNumber(row.impact_estimate_bps, 0)} bps` : "—"}</td>
          </tr>
        ))}
        {unsized.map((row) => (
          <tr key={row.ticker}>
            <th scope="row">{row.ticker}</th>
            <td>{fmtMoney(row.position_usd || 0)}</td>
            <td>—</td>
            <td>—</td>
            <td className="tone-warning">ADV unavailable</td>
          </tr>
        ))}</tbody>
        </table>
      </div>
      <p className="muted">{capacity.basis}</p>
    </Panel>
  );
}

function ClientGradeRiskPack({ pack }: { pack?: ClientRiskPack }) {
  if (!pack) return null;
  if (!pack.available) {
    return (
      <Panel title="Client-Grade Risk Pack" meta="real data required">
        <EmptyState title="Risk pack locked" body={pack.required_action || "Eligible real model histories are required before rendering client-grade risk language."} />
      </Panel>
    );
  }
  return (
    <Panel title="Client-Grade Risk Pack" meta={titleCase(pack.summary.risk_posture || "advisor review")}>
      <div className="risk-pack-grid">
        <section>
          <h2>Benchmark-Relative Drawdown</h2>
          <div className="metric-grid">
            <StatTile label="Benchmark" value={pack.benchmark_relative_drawdown.benchmark_symbol || pack.summary.benchmark_symbol || "—"} />
            <StatTile label="Rel. drawdown" value={fmtPct(pack.benchmark_relative_drawdown.relative_drawdown_pct)} tone="warning" />
            <StatTile label="Beta" value={fmtNumber(pack.benchmark_relative_drawdown.beta, 2)} />
            <StatTile label="Tracking error" value={fmtPct(pack.benchmark_relative_drawdown.tracking_error_pct)} />
          </div>
          <p className="muted">{pack.benchmark_relative_drawdown.interpretation}</p>
        </section>
        <section>
          <h2>What Would Break This Model</h2>
          <div className="risk-break-list">
            {pack.what_would_break_this_model.map((row) => (
              <article key={`${row.driver}-${row.severity}`}>
                <strong>{row.driver}</strong>
                <span className={row.severity === "high" ? "tone-negative" : row.severity === "medium" ? "tone-warning" : ""}>{titleCase(row.severity)}</span>
                <small>Breakpoint Evidence: {row.trigger} · {row.evidence}</small>
                <p>{row.language}</p>
              </article>
            ))}
          </div>
        </section>
      </div>
      <section className="dashboard-grid three risk-pack-panels">
        <div>
          <h2>Stress Scenarios</h2>
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable client risk pack stress scenarios">
            <table className="terminal-data-table risk-pack-data-table">
              <thead><tr><th scope="col">Scenario</th><th scope="col">Impact</th><th scope="col">Severity</th></tr></thead>
              <tbody>{pack.stress_scenarios.slice(0, 5).map((row) => (
              <tr key={row.scenario}>
                <th scope="row">{row.scenario}<small>{row.what_it_tests}</small></th>
                <td className={row.portfolio_impact_pct < 0 ? "tone-negative" : "tone-positive"}>{fmtPct(row.portfolio_impact_pct)}</td>
                <td>{titleCase(row.severity)}</td>
              </tr>
            ))}</tbody>
            </table>
          </div>
        </div>
        <div>
          <h2>Historical Stress Replay</h2>
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable historical stress replay">
            <table className="terminal-data-table risk-pack-data-table">
              <thead><tr><th scope="col">Observed Window</th><th scope="col">Impact</th><th scope="col">Evidence</th></tr></thead>
              <tbody>{pack.historical_stress_replay.slice(0, 5).map((row) => (
              <tr key={`${row.scenario}-${row.window_days}`}>
                <th scope="row">{row.scenario}<small>{row.start_date} to {row.end_date}</small></th>
                <td className={row.portfolio_impact_pct < 0 ? "tone-negative" : "tone-positive"}>{fmtPct(row.portfolio_impact_pct)}</td>
                <td>{titleCase(row.basis)}</td>
              </tr>
            ))}</tbody>
            </table>
          </div>
        </div>
        <div>
          <h2>Concentration Warnings</h2>
          <div className="risk-break-list compact">
            {pack.concentration_warnings.slice(0, 5).map((row) => (
              <article key={`${row.type}-${row.title}`}>
                <strong>{row.title}</strong>
                <span>{titleCase(row.severity)}</span>
                <p>{row.detail}</p>
              </article>
            ))}
          </div>
        </div>
        <div>
          <h2>Liquidity Watchlist</h2>
          <div className="risk-break-list compact">
            {pack.liquidity_flags.items.slice(0, 5).map((row) => (
              <article key={`${row.ticker}-${row.flag}`}>
                <strong>{row.ticker || "Holding"}</strong>
                <span>{titleCase(row.flag || "review")} · {fmtPct(row.weight_pct)} · Observed ADV {row.observed_adv_usd ? fmtMoney(row.observed_adv_usd) : "unavailable"}</span>
                <p>{row.language}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
      {pack.correlation_clusters.length > 0 && (
        <div className="risk-pack-clusters">
          <h2>Correlation Clusters</h2>
          {pack.correlation_clusters.slice(0, 3).map((cluster) => (
            <article key={`${cluster.name}-${cluster.type}`}>
              <strong>{cluster.name || "Correlation cluster"}</strong>
              <span>{cluster.language}</span>
            </article>
          ))}
        </div>
      )}
      <p className="muted">{pack.disclaimer}</p>
    </Panel>
  );
}

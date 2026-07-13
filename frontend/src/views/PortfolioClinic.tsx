import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ClinicResponse, ModelSummary } from "../api/types";
import { AICopilotPanel, clinicCopilotActions } from "../components/ai/AICopilotPanel";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { DonutChart, MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtAuto, fmtNumber, fmtPct, titleCase } from "../utils/format";

export function PortfolioClinic({
  models,
  selectedModel,
  onSelectModel,
}: {
  models: ModelSummary[];
  selectedModel?: string;
  onSelectModel: (id: string) => void;
}) {
  // No first-model fallback: the clinic diagnoses the model the operator chose.
  const defaultModelId = selectedModel || "";
  const [modelId, setModelId] = useState(defaultModelId);
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<ClinicResponse>({ failureMessage: "Portfolio Clinic failed." });
  const modelOptions = models.length > 0
    ? models.map((model) => ({ value: model.id, label: `${model.name} · ${model.mandate_label}` }))
    : [{ value: "", label: "No models imported" }];
  const selectedModelSummary = models.find((model) => model.id === modelId) || null;
  const copilotPayload = useMemo<Record<string, unknown> | null>(() => {
    if (!payload) return null;
    return {
      clinic: payload,
      model_summary: selectedModelSummary,
      disclaimer: payload.disclaimer,
    };
  }, [payload, selectedModelSummary]);

  const run = useCallback((requestedModelId: string) => {
    if (!requestedModelId) return;
    onSelectModel(requestedModelId);
    void load(requestedModelId, () => api.clinic(requestedModelId));
  }, [load, onSelectModel]);

  useEffect(() => {
    if (!defaultModelId || isCurrentTarget(defaultModelId)) return;
    setModelId(defaultModelId);
    run(defaultModelId);
  }, [defaultModelId, isCurrentTarget, run]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Portfolio Clinic</div><h1>Model diagnostics and hypothetical improvements</h1><p>Long-only, mandate-aware diagnostics. Suggestions are research prompts, not orders.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); run(modelId); }}>
          <label>Model<TerminalSelect ariaLabel="Portfolio Clinic model" value={modelId} onChange={setModelId} options={modelOptions} disabled={models.length === 0} /></label>
          <button type="submit" disabled={models.length === 0}>Diagnose</button>
        </form>
      </header>
      {error && <div className="notice danger" role="alert">{error}</div>}
      {isLoading && <div className="loading" role="status">Running mandate-aware model diagnostics...</div>}
      {selectedModelSummary && (
        <div className="source-context-strip">
          <span><b>Coverage</b>{selectedModelSummary.real_coverage_count || 0}/{selectedModelSummary.n_holdings} holdings</span>
          <span><b>State</b>{selectedModelSummary.coverage_state || "pending"}</span>
          <span><b>Missing</b>{selectedModelSummary.missing_tickers?.join(", ") || "None"}</span>
        </div>
      )}
      {models.length === 0 ? <LockedClinicState /> : null}
      {payload && <DataQualityBanner payload={payload} />}
      {payload && (
        <>
          {!payload.eligible_for_real_research && (
            <div className="strategy-demo-warning" role="status">
              <strong>Clinic result is hypothetical — not an order.</strong>
              <span>{payload.required_action || payload.reason || "Resolve simulated, sample, or missing holding histories before treating diagnostics as real evidence."}</span>
            </div>
          )}
          {(payload.warnings || []).length > 0 && (
            <div className="warning-list">{(payload.warnings || []).map((warning) => <span key={warning}>{warning}</span>)}</div>
          )}
          <section className="dashboard-grid three">
            <Panel title={payload.name} meta={payload.mandate.label}>
              <div className="metric-grid">
                <StatTile label="HHI" value={fmtNumber(payload.diagnostics.hhi, 2)} />
                <StatTile label="Effective holdings" value={fmtNumber(payload.diagnostics.effective_holdings, 1)} />
                <StatTile label="Top weight" value={fmtPct(payload.diagnostics.top_weight_pct)} />
                <StatTile label="Vol gap" value={fmtPct(payload.diagnostics.volatility_gap_pct)} tone="warning" />
              </div>
            </Panel>
            <Panel title="Suggestions" meta="hypothetical">
              {payload.suggestions.length === 0 ? (
                <EmptyState title="No hypothetical changes" body="The current clinic rules did not generate changes." />
              ) : payload.suggestions.map((suggestion) => (
                <div className={`suggestion-card suggestion-${safeSuggestionType(suggestion.type)}`} key={`${suggestion.type}-${suggestion.ticker || "model"}`}>
                  <strong>{titleCase(suggestion.type)}{suggestion.ticker ? ` · ${suggestion.ticker}` : ""}</strong>
                  <span>{suggestion.rationale}</span>
                  <small>{fmtPct(suggestion.current_weight * 100)} to {fmtPct(suggestion.suggested_weight * 100)}</small>
                </div>
              ))}
            </Panel>
            <Panel title="Verify Before Acting">
              <ul className="checklist">
                <li>Confirm every holding uses live or uploaded history.</li>
                <li>Review mandate fit and suitability outside Helios.</li>
                <li>Re-run reports after any model or data-source change.</li>
              </ul>
            </Panel>
          </section>
          <section className="dashboard-grid">
            <Panel title="Risk Contribution">
              <div className="clinic-risk-chart">
                <DonutChart
                  segments={payload.risk_contributions.slice(0, 8).map((item) => ({ label: item.ticker, value: item.mrc_pct, tone: item.mrc_pct > 18 ? "negative" : "warning" }))}
                  centerLabel="Risk share"
                  centerValue={`${fmtNumber(payload.risk_contributions.slice(0, 8).reduce((sum, item) => sum + item.mrc_pct, 0), 1)}%`}
                />
                <MiniBars rows={payload.risk_contributions.slice(0, 10).map((item) => ({ label: item.ticker, value: item.mrc_pct, tone: "negative" }))} />
              </div>
            </Panel>
            <Panel title="Before / After Estimates">
              <div className="comparison-grid">
                <EstimateCard title="Current model" estimates={payload.before.estimates} />
                <EstimateCard title="Hypothetical after" estimates={payload.after.estimates} />
              </div>
            </Panel>
          </section>
          {payload.suggestions.length > 0 && (
            <Panel title="Suggested Changes Table" meta="hypothetical/not an order">
              <div className="suggestion-table" tabIndex={0} aria-label="Scrollable hypothetical suggestion table">
                <div><span>Type</span><span>Ticker</span><span>Current</span><span>Suggested</span><span>Rationale</span></div>
                {payload.suggestions.map((suggestion) => (
                  <div key={`${suggestion.type}-${suggestion.ticker || "model"}-${suggestion.suggested_weight}`}>
                    <strong>{titleCase(suggestion.type)}</strong>
                    <span>{suggestion.ticker || "Model"}</span>
                    <span>{fmtPct(suggestion.current_weight * 100)}</span>
                    <span>{fmtPct(suggestion.suggested_weight * 100)}</span>
                    <span>{suggestion.rationale}</span>
                  </div>
                ))}
              </div>
            </Panel>
          )}
          <Panel title="Explanation"><p className="lead">{payload.explanation}</p>{payload.refusals.length > 0 && <DriverList rows={payload.refusals} />}</Panel>
          <AICopilotPanel
            contextLabel={`${payload.name} clinic review`}
            payload={copilotPayload}
            dataMode={payload.data_mode}
            actions={clinicCopilotActions}
          />
        </>
      )}
    </div>
  );
}

function DriverList({ rows }: { rows: string[] }) {
  return <ul className="checklist">{rows.map((row) => <li key={row}>{row}</li>)}</ul>;
}

function LockedClinicState() {
  return (
    <section className="dashboard-grid">
      <Panel title="Portfolio Clinic Gate" meta="model required">
        <div className="locked-state-panel">
          <strong>No model imported</strong>
          <p>Upload a client model file before running Portfolio Clinic diagnostics.</p>
          <div>
            <span className="source-pill source-excluded">no hypothetical output</span>
            <span className="source-pill source-model">model file required</span>
          </div>
        </div>
      </Panel>
      <Panel title="Required Evidence" meta="real-data gate">
        <div className="radar-preview-table gate-checklist-table clinic-gate-table">
          <div><span>Area</span><span>Status</span><span>Required evidence</span><span>Next step</span></div>
          <div className="locked-table-row">
            <strong>Holdings coverage<small>Every analyzed holding needs eligible price history.</small></strong>
            <span>Pending</span>
            <span>Imported model plus live/uploaded histories</span>
            <em>Upload a model, then fetch or upload missing histories.</em>
          </div>
          <div className="locked-table-row">
            <strong>Clinic suggestions<small>Suggestions remain disabled until the model exists.</small></strong>
            <span>Blocked</span>
            <span>Mandate-aware model diagnostics</span>
            <em>Run diagnostics after model coverage is available.</em>
          </div>
        </div>
      </Panel>
    </section>
  );
}

function EstimateCard({ title, estimates }: { title: string; estimates: Record<string, number> }) {
  return (
    <div className="estimate-card">
      <strong>{title}</strong>
      <dl>
        {Object.entries(estimates).map(([key, value]) => (
          <div key={key}><dt>{titleCase(key)}</dt><dd>{fmtAuto(value)}</dd></div>
        ))}
      </dl>
    </div>
  );
}

function safeSuggestionType(type?: string) {
  if (type === "trim" || type === "add" || type === "reduce" || type === "increase" || type === "rebalance") return type;
  return "rebalance";
}

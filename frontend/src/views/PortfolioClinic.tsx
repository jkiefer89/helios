import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ClinicResponse, ModelSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
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
  const defaultModelId = selectedModel || models[0]?.id || "";
  const [modelId, setModelId] = useState(defaultModelId);
  const [payload, setPayload] = useState<ClinicResponse | null>(null);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);

  const run = async (requestedModelId = modelId) => {
    if (!requestedModelId) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      onSelectModel(requestedModelId);
      const result = await api.clinic(requestedModelId);
      if (requestId !== requestSeq.current) return;
      setPayload(result);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Portfolio Clinic failed.");
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
        <div><div className="section-label">Portfolio Clinic</div><h1>Model diagnostics and hypothetical improvements</h1><p>Long-only, mandate-aware diagnostics. Suggestions are research prompts, not orders.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void run(); }}>
          <label>Model<select value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.mandate_label}</option>)}</select></label>
          <button type="submit">Diagnose</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {models.length === 0 ? <EmptyState title="No model imported" body="Upload a model file before running Portfolio Clinic." /> : null}
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
              <MiniBars rows={payload.risk_contributions.slice(0, 10).map((item) => ({ label: item.ticker, value: item.mrc_pct, tone: "negative" }))} />
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
        </>
      )}
    </div>
  );
}

function DriverList({ rows }: { rows: string[] }) {
  return <ul className="checklist">{rows.map((row) => <li key={row}>{row}</li>)}</ul>;
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

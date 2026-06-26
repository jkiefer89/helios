import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ClinicResponse, ModelSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtPct } from "../utils/format";

export function PortfolioClinic({
  models,
  selectedModel,
  onSelectModel,
}: {
  models: ModelSummary[];
  selectedModel?: string;
  onSelectModel: (id: string) => void;
}) {
  const [modelId, setModelId] = useState(selectedModel || models[0]?.id || "");
  const [payload, setPayload] = useState<ClinicResponse | null>(null);
  const [error, setError] = useState("");

  const run = async () => {
    if (!modelId) return;
    try {
      setError("");
      onSelectModel(modelId);
      setPayload(await api.clinic(modelId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Portfolio Clinic failed.");
    }
  };

  useEffect(() => {
    if (!modelId && models[0]) setModelId(models[0].id);
  }, [models, modelId]);

  useEffect(() => {
    if (modelId) void run();
  }, []);

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
                <div className={`suggestion-card suggestion-${suggestion.type}`} key={`${suggestion.type}-${suggestion.ticker || "model"}`}>
                  <strong>{suggestion.type.replace("_", " ")}{suggestion.ticker ? ` · ${suggestion.ticker}` : ""}</strong>
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
              <MiniBars rows={[
                { label: "Before vol", value: payload.before.estimates.annual_vol_pct || 0, tone: "neutral" },
                { label: "After vol", value: payload.after.estimates.annual_vol_pct || 0, tone: "positive" },
                { label: "Before HHI", value: (payload.before.estimates.hhi || 0) * 100, tone: "neutral" },
                { label: "After HHI", value: (payload.after.estimates.hhi || 0) * 100, tone: "positive" },
              ]} />
            </Panel>
          </section>
          <Panel title="Explanation"><p className="lead">{payload.explanation}</p>{payload.refusals.length > 0 && <DriverList rows={payload.refusals} />}</Panel>
        </>
      )}
    </div>
  );
}

function DriverList({ rows }: { rows: string[] }) {
  return <ul className="checklist">{rows.map((row) => <li key={row}>{row}</li>)}</ul>;
}

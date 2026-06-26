import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AnalysisResponse, DataMode, ModelSummary, ProvenancePayload, TickerSummary } from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { LineChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtPct, titleCase } from "../utils/format";

export function Analysis({
  tickers,
  models,
  selectedInstrument,
  selectedModel,
  onSelectInstrument,
  onSelectModel,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
}) {
  const defaultTarget = selectedModel ? `model:${selectedModel}` : selectedInstrument ? `instrument:${selectedInstrument}` : tickers[0] ? `instrument:${tickers[0].symbol}` : "";
  const [target, setTarget] = useState(defaultTarget);
  const [horizon, setHorizon] = useState<string | number>(21);
  const [payload, setPayload] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState("");
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const load = async () => {
    const [kind, id] = (target || defaultTarget).split(":");
    if (!kind || !id) return;
    try {
      setError("");
      if (kind === "model") {
        onSelectModel(id);
        setPayload(await api.analyzeModel(id, horizon));
      } else {
        onSelectInstrument(id);
        setPayload(await api.analyzeInstrument(id, Number(horizon) || 21));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed.");
    }
  };

  useEffect(() => {
    if (!target && defaultTarget) setTarget(defaultTarget);
  }, [defaultTarget, target]);

  useEffect(() => {
    if (target || defaultTarget) void load();
  }, []);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Analysis</div><h1>Instrument and model detail</h1><p>Legacy analytics payloads rendered in the React terminal.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label>Target<select value={target} onChange={(event) => setTarget(event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label>Horizon<input value={horizon} onChange={(event) => setHorizon(event.target.value)} /></label>
          <button type="submit">Analyze</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? <EmptyState title="Select a target" body="Choose an instrument or model to load analytics." /> : (
        <>
          <DataQualityBanner payload={analysisQuality(payload)} />
          <Panel title={payload.name} meta={payload.source ? <SourcePill source={payload.source} /> : payload.mandate?.label}>
            <div className="signal-strip">
              <strong className={`signal-action action-${payload.signal.action.toLowerCase()}`}>{payload.signal.action}</strong>
              <p>{payload.signal.headline_rationale || payload.signal.rationale}</p>
              <span>{fmtNumber(payload.signal.conviction_pct, 0)}% conviction</span>
            </div>
            {payload.signal.caveats?.length ? <div className="warning-list">{payload.signal.caveats.map((caveat) => <span key={caveat}>{caveat}</span>)}</div> : null}
          </Panel>
          <section className="dashboard-grid three">
            <Panel title="Metrics">
              <div className="metric-grid">
                {Object.entries(payload.metrics).slice(0, 8).map(([key, value]) => (
                  <StatTile key={key} label={titleCase(key)} value={typeof value === "number" ? fmtNumber(value, 2) : String(value ?? "—")} />
                ))}
              </div>
            </Panel>
            <Panel title="Forecast">
              <KeyObject data={payload.forecast} />
            </Panel>
            <Panel title="Backtest">
              <KeyObject data={payload.backtest} />
            </Panel>
          </section>
          <Panel title="Price and Trend">
            <LineChart labels={payload.series.dates} series={[
              { label: "Close", values: payload.series.close, tone: "info" },
              { label: "SMA 50", values: payload.series.sma50 || [], tone: "positive" },
              { label: "SMA 200", values: payload.series.sma200 || [], tone: "warning" },
            ]} />
          </Panel>
          {payload.holdings && (
            <Panel title="Holdings">
              <div className="holdings-table">
                {payload.holdings.map((holding) => (
                  <div key={String(holding.ticker)}>
                    <strong>{String(holding.ticker)}</strong>
                    <span>{fmtPct(Number(holding.weight) * 100)}</span>
                    <span>{String(holding.source || "unavailable")}</span>
                    <span>{String(holding.signal || "—")}</span>
                  </div>
                ))}
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

function analysisQuality(payload: AnalysisResponse): ProvenancePayload {
  if (payload.source) {
    const real = ["live", "upload"].includes(payload.source);
    return {
      data_mode: real ? "real" : "demo",
      display_label: real ? "Real/Uploaded Analysis Data" : "Demo Analysis Data",
      eligible_for_real_research: real,
      reason: real
        ? "This analysis uses a live or uploaded price history."
        : "This view is using bundled sample data for workflow demonstration only.",
      required_action: real ? "" : "Fetch live data or upload client price history before treating this as research evidence.",
      data_provenance: {
        source_counts: { [payload.source]: 1 },
        data_mode: real ? "real" : "demo",
      },
    };
  }

  const provenance = (payload.provenance || {}) as Record<string, unknown>;
  const simulatedWeight = Number(provenance.simulated_weight_pct || 0);
  const sampleCount = Number(provenance.n_sample || 0);
  const excludedCount = Number(provenance.n_excluded || 0);
  const real = simulatedWeight === 0 && sampleCount === 0 && excludedCount === 0;
  const mode: DataMode = real ? "real" : "mixed";
  return {
    data_mode: mode,
    display_label: real ? "Real Model Analysis Data" : "Mixed Model Analysis Data",
    eligible_for_real_research: real,
    reason: real
      ? "Model analysis is based on resolved live or uploaded holding histories."
      : "Model analysis includes sample, simulated, or excluded holding history and requires verification.",
    required_action: real ? "" : "Use Portfolio Clinic and reports only after replacing missing or simulated holding history.",
    data_provenance: {
      ...provenance,
      data_mode: mode,
    },
  };
}

function KeyObject({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="key-values dense">
      {Object.entries(data).slice(0, 12).map(([key, value]) => (
        <span key={key}><b>{displayValue(value)}</b><small>{titleCase(key)}</small></span>
      ))}
    </div>
  );
}

function displayValue(value: unknown): string {
  if (typeof value === "number") return fmtNumber(value, 2);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return `${value.length} items`;
  if (value && typeof value === "object") return "Details";
  return String(value ?? "—");
}

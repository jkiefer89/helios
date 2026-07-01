import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { AnalysisResponse, DataMode, ModelSummary, ProvenancePayload, TickerSummary } from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { DrawdownChart, HistogramChart, PriceTrendChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtAuto, fmtNumber, fmtPct, titleCase } from "../utils/format";

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
  const requestSeq = useRef(0);
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const load = async (requestedTarget = target || defaultTarget) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      if (kind === "model") {
        onSelectModel(id);
        const result = await api.analyzeModel(id, horizon);
        if (requestId !== requestSeq.current) return;
        setPayload(result);
      } else {
        onSelectInstrument(id);
        const result = await api.analyzeInstrument(id, Number(horizon) || 21);
        if (requestId !== requestSeq.current) return;
        setPayload(result);
      }
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Analysis failed.");
    }
  };

  useEffect(() => {
    if (!defaultTarget) return;
    setTarget(defaultTarget);
    void load(defaultTarget);
  }, [defaultTarget]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Analysis</div><h1>Instrument and model detail</h1><p>Legacy analytics payloads rendered in the React terminal.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label>Target<TerminalSelect ariaLabel="Analysis target" value={target} onChange={setTarget} options={options} /></label>
          <label>Horizon<input value={horizon} onChange={(event) => setHorizon(event.target.value)} /></label>
          <button type="submit">Analyze</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? <EmptyState title="Select a target" body="Choose an instrument or model to load analytics." /> : (
        <AnalysisPayload payload={payload} />
      )}
    </div>
  );
}

function AnalysisPayload({ payload }: { payload: AnalysisResponse }) {
  const quality = analysisQuality(payload);
  const eligible = quality.eligible_for_real_research === true;
  const actionClass = eligible ? safeAction(payload.signal.action) : "preview";
  const signalLabel = eligible ? payload.signal.action : "PREVIEW";
  const panelSuffix = eligible ? "" : " preview";
  const dailyReturns = pctReturns(payload.series.close);
  const drawdown = drawdownSeries(payload.series.close);
  return (
    <>
      <DataQualityBanner payload={quality} />
      <Panel title={`${payload.name}${eligible ? "" : " preview"}`} meta={payload.source ? <SourcePill source={payload.source} /> : payload.mandate?.label}>
        <div className={`signal-strip ${eligible ? "" : "signal-strip--preview"}`}>
          <strong className={`signal-action action-${actionClass}`}>{signalLabel}</strong>
          <p>{payload.signal.headline_rationale || payload.signal.rationale}</p>
          <span>{eligible ? `${fmtNumber(payload.signal.conviction_pct, 0)}% conviction` : "Research locked"}</span>
        </div>
        {!eligible && <div className="warning-list"><span>{quality.required_action || "Replace demo or mixed inputs before treating this as research evidence."}</span></div>}
        {payload.signal.caveats?.length ? <div className="warning-list">{payload.signal.caveats.map((caveat) => <span key={caveat}>{caveat}</span>)}</div> : null}
      </Panel>
      <section className="dashboard-grid three">
        <Panel title={`Metrics${panelSuffix}`}>
          <div className="metric-grid">
            {Object.entries(payload.metrics).slice(0, 8).map(([key, value]) => (
              <StatTile key={key} label={titleCase(key)} value={fmtAuto(value)} />
            ))}
          </div>
        </Panel>
        <Panel title={`Forecast${panelSuffix}`}>
          <KeyObject data={payload.forecast} />
        </Panel>
        <Panel title={`Backtest${panelSuffix}`}>
          <KeyObject data={payload.backtest} />
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title={`Price and Trend${panelSuffix}`} className="span-2">
          <PriceTrendChart labels={payload.series.dates} close={payload.series.close} sma50={payload.series.sma50 || []} sma200={payload.series.sma200 || []} />
        </Panel>
        <Panel title={`Drawdown${panelSuffix}`}>
          <DrawdownChart labels={payload.series.dates} values={drawdown} height={160} />
        </Panel>
        <Panel title={`Daily Return Distribution${panelSuffix}`}>
          <HistogramChart values={dailyReturns} label="Daily return %" buckets={9} tone={eligible ? "info" : "warning"} />
        </Panel>
      </section>
      {payload.holdings && (
        <Panel title={`Holdings${panelSuffix}`}>
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
  );
}

function safeAction(action?: string) {
  const normalized = String(action || "").toLowerCase();
  if (normalized === "buy" || normalized === "sell" || normalized === "hold" || normalized === "review") return normalized;
  return "review";
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
  if (Array.isArray(value)) return `${value.length} items`;
  if (value && typeof value === "object") return "Details";
  return fmtAuto(value);
}

function pctReturns(values: Array<number | null>): Array<number | null> {
  return values.map((value, index) => {
    const previous = values[index - 1];
    if (typeof value !== "number" || typeof previous !== "number" || !Number.isFinite(value) || !Number.isFinite(previous) || previous === 0) return null;
    return ((value / previous) - 1) * 100;
  });
}

function drawdownSeries(values: Array<number | null>): Array<number | null> {
  let peak = 0;
  return values.map((value) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    peak = Math.max(peak || value, value);
    return peak > 0 ? ((value / peak) - 1) * 100 : 0;
  });
}

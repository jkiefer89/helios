import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ModelSummary, ReportResponse, TickerSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { titleCase } from "../utils/format";

export function Reports({
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
  const [payload, setPayload] = useState<ReportResponse | null>(null);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const build = async (requestedTarget = target || defaultTarget) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      if (kind === "model") onSelectModel(id);
      else onSelectInstrument(id);
      const nextPayload = kind === "model" ? await api.reportModel(id) : await api.reportInstrument(id);
      if (requestId !== requestSeq.current) return;
      setPayload(nextPayload);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Report build failed.");
    }
  };

  useEffect(() => {
    if (!defaultTarget) return;
    setTarget(defaultTarget);
    void build(defaultTarget);
  }, [defaultTarget]);

  return (
    <div className="view-stack report-view">
      <header className="view-head no-print">
        <div><div className="section-label">Analysis-Only Report</div><h1>Evidence preview pack</h1><p>Every preview opens with data quality and analysis-only caveats.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void build(); }}>
          <label>Target<select value={target} onChange={(event) => setTarget(event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <button type="submit">Build preview</button>
          <button type="button" onClick={() => window.print()}>{payload?.eligible_for_real_research ? "Print evidence pack" : "Print preview"}</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? (
        <EmptyState title="Select a report target" body="Choose an instrument or model to build an analysis-only report." />
      ) : (
        <ReportSheet payload={payload} />
      )}
    </div>
  );
}

function ReportSheet({ payload }: { payload: ReportResponse }) {
  const previewLocked = !payload.eligible_for_real_research;
  const sections = previewLocked ? maskPreviewSections(payload.sections) : payload.sections;
  return (
    <article className="report-sheet">
      <header>
        <div><div className="section-label">Helios Analysis-Only Report Preview</div><h1>{payload.title}</h1><p>{payload.timestamp}</p></div>
        <span className="badge">{payload.kind}</span>
      </header>
      <DataQualityBanner payload={payload} compact />
      {previewLocked && (
        <div className="report-watermark">{payload.display_label || "Research locked"} · preview only</div>
      )}
      {payload.warnings && payload.warnings.length > 0 && <div className="warning-list">{payload.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
      <div className="report-grid">
        {Object.entries(sections).map(([key, value]) => (
          <Panel key={key} title={titleCase(key)}>
            <ReportValue value={value} />
          </Panel>
        ))}
      </div>
      <p className="report-disclaimer">{payload.disclaimer}</p>
    </article>
  );
}

function ReportValue({ value }: { value: unknown }) {
  if (value == null) return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    return value.length ? <ul>{value.map((item, index) => <li key={index}><ReportValue value={item} /></li>)}</ul> : <span className="muted">None</span>;
  }
  if (typeof value === "object") {
    return (
      <dl className="report-dl">
        {Object.entries(value as Record<string, unknown>).map(([key, nested]) => (
          <div key={key}><dt>{titleCase(key)}</dt><dd><ReportValue value={nested} /></dd></div>
        ))}
      </dl>
    );
  }
  if (typeof value === "boolean") return <span>{value ? "Yes" : "No"}</span>;
  return <span>{String(value)}</span>;
}

function maskPreviewSections(sections: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(sections).map(([key, value]) => [key, maskPreviewValue("", value)]));
}

function maskPreviewValue(key: string, value: unknown): unknown {
  const normalized = key.toLowerCase();
  if (normalized === "action") return "PREVIEW";
  if (normalized === "conviction_pct" || normalized === "signal_score") return "Research locked";
  if (Array.isArray(value)) return value.map((item) => maskPreviewValue(key, item));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([nestedKey, nestedValue]) => [nestedKey, maskPreviewValue(nestedKey, nestedValue)]));
  }
  return value;
}

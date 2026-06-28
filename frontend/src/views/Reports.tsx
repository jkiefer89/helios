import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { DataStatusResponse, ModelSummary, ReportResponse, TickerSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtAuto, fmtNumber, fmtPct, fmtTimestamp, titleCase } from "../utils/format";

export function Reports({
  tickers,
  models,
  selectedInstrument,
  selectedModel,
  onSelectInstrument,
  onSelectModel,
  dataStatus,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
  dataStatus: DataStatusResponse | null;
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
          <label>Target<TerminalSelect ariaLabel="Report target" value={target} onChange={setTarget} options={options} /></label>
          <button type="submit">Build preview</button>
          <button type="button" onClick={() => window.print()}>{payload?.eligible_for_real_research ? "Print evidence pack" : "Print preview"}</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? (
        <EmptyState title="Select a report target" body="Choose an instrument or model to build an analysis-only report." />
      ) : (
        <ReportSheet payload={payload} dataStatus={dataStatus} />
      )}
    </div>
  );
}

function ReportSheet({ payload, dataStatus }: { payload: ReportResponse; dataStatus: DataStatusResponse | null }) {
  const previewLocked = !payload.eligible_for_real_research;
  const sections = previewLocked ? maskPreviewSections(payload.sections) : payload.sections;
  const title = reportTitle(payload);
  return (
    <article className="report-sheet">
      <header>
        <div><div className="section-label">Helios Analysis-Only Report Preview</div><h1>{title}</h1><p>{fmtTimestamp(payload.timestamp)}</p></div>
        <span className="badge">{payload.kind}</span>
      </header>
      <DataQualityBanner payload={payload} compact />
      <PersistenceSnapshot dataStatus={dataStatus} />
      {previewLocked && (
        <div className="report-watermark">{payload.display_label || "Research locked"} · preview only</div>
      )}
      {payload.warnings && payload.warnings.length > 0 && <div className="warning-list">{payload.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
      <div className="report-grid">
        {Object.entries(sections).map(([key, value]) => (
          <Panel key={key} title={titleCase(key)} className={`report-section report-section-${cssName(key)}`}>
            <ReportValue value={value} labelKey={key} />
          </Panel>
        ))}
      </div>
      <p className="report-disclaimer">{payload.disclaimer}</p>
    </article>
  );
}

function PersistenceSnapshot({ dataStatus }: { dataStatus: DataStatusResponse | null }) {
  if (!dataStatus) return null;
  return (
    <Panel title="Data Quality / Persistence" className="report-persistence-panel" meta={dataStatus.database.available ? "SQLite ready" : "persistence warning"}>
      <dl className="report-dl">
        <div><dt>Database Available</dt><dd>{dataStatus.database.available ? "Yes" : "No"}</dd></div>
        <div><dt>Eligible Price Histories</dt><dd>{dataStatus.real_instrument_count}</dd></div>
        <div><dt>Persisted Models</dt><dd>{dataStatus.persisted_model_count}</dd></div>
        <div><dt>Last Refresh</dt><dd>{fmtTimestamp(dataStatus.last_refresh?.attempted_at)}</dd></div>
        <div><dt>Missing Holdings</dt><dd>{dataStatus.missing_data.missing_tickers.join(", ") || "None"}</dd></div>
      </dl>
      {dataStatus.warnings.length > 0 && <div className="warning-list compact-list">{dataStatus.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
    </Panel>
  );
}

function ReportValue({ value, labelKey = "" }: { value: unknown; labelKey?: string }) {
  if (value == null) return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    return value.length ? <ul className="report-list">{value.map((item, index) => <li key={index}><ReportValue value={item} labelKey={labelKey} /></li>)}</ul> : <span className="muted">None</span>;
  }
  if (typeof value === "object") {
    return (
      <dl className="report-dl">
        {Object.entries(value as Record<string, unknown>).map(([key, nested]) => (
          <div key={key}><dt>{titleCase(key)}</dt><dd><ReportValue value={nested} labelKey={key} /></dd></div>
        ))}
      </dl>
    );
  }
  if (typeof value === "string" && looksLikeTimestamp(value)) return <span>{fmtTimestamp(value)}</span>;
  return <span>{formatReportPrimitive(labelKey, value)}</span>;
}

function reportTitle(payload: ReportResponse) {
  const lowerTitle = payload.title.toLowerCase();
  const mode = String(payload.data_mode || payload.data_provenance?.data_mode || "").toLowerCase();
  const label = String(payload.display_label || payload.data_provenance?.display_label || "").toLowerCase();
  if (mode === "invalid_for_research" || label.includes("blocked")) return "Data Quality Blocked — Upload Real History";
  if (mode === "demo" && !lowerTitle.includes("demo report")) return `Demo Report — Not Real Market Evidence: ${payload.title}`;
  return payload.title;
}

function looksLikeTimestamp(value: string) {
  return /^\d{4}-\d{2}-\d{2}T/.test(value) || /^\d{4}-\d{2}-\d{2}\s+\d{2}:/.test(value);
}

function cssName(key: string) {
  return key.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function formatReportPrimitive(key: string, value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) return fmtAuto(value);
  const normalized = key.toLowerCase();
  if (normalized.endsWith("_pct") || normalized.includes("pct") || normalized.includes("return") || normalized.includes("drawdown")) return fmtPct(value);
  if (normalized.endsWith("_bps") || normalized.includes("bps")) return `${fmtNumber(value, 0)} bps`;
  if (normalized.includes("score") || normalized.includes("quality") || normalized.includes("r2") || normalized.includes("rmse")) return fmtNumber(value, 1);
  return Number.isInteger(value) ? fmtNumber(value, 0) : fmtNumber(value, 2);
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

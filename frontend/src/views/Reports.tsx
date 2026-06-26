import { useEffect, useMemo, useState } from "react";
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
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
}) {
  const defaultTarget = selectedModel ? `model:${selectedModel}` : selectedInstrument ? `instrument:${selectedInstrument}` : tickers[0] ? `instrument:${tickers[0].symbol}` : "";
  const [target, setTarget] = useState(defaultTarget);
  const [payload, setPayload] = useState<ReportResponse | null>(null);
  const [error, setError] = useState("");
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const build = async () => {
    const [kind, id] = (target || defaultTarget).split(":");
    if (!kind || !id) return;
    try {
      setError("");
      setPayload(kind === "model" ? await api.reportModel(id) : await api.reportInstrument(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Report build failed.");
    }
  };

  useEffect(() => {
    if (!target && defaultTarget) setTarget(defaultTarget);
  }, [defaultTarget, target]);

  useEffect(() => {
    if (target || defaultTarget) void build();
  }, []);

  return (
    <div className="view-stack report-view">
      <header className="view-head no-print">
        <div><div className="section-label">Advisor Report</div><h1>Printable evidence pack</h1><p>Every report opens with data quality and analysis-only caveats.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void build(); }}>
          <label>Target<select value={target} onChange={(event) => setTarget(event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <button type="submit">Build</button>
          <button type="button" onClick={() => window.print()}>Print</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? (
        <EmptyState title="Select a report target" body="Choose an instrument or model to build an analysis-only report." />
      ) : (
        <article className="report-sheet">
          <header>
            <div><div className="section-label">Helios Advisor Report</div><h1>{payload.title}</h1><p>{payload.timestamp}</p></div>
            <span className="badge">{payload.kind}</span>
          </header>
          <DataQualityBanner payload={payload} compact />
          {payload.warnings && payload.warnings.length > 0 && <div className="warning-list">{payload.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
          <div className="report-grid">
            {Object.entries(payload.sections).map(([key, value]) => (
              <Panel key={key} title={titleCase(key)}>
                <ReportValue value={value} />
              </Panel>
            ))}
          </div>
          <p className="report-disclaimer">{payload.disclaimer}</p>
        </article>
      )}
    </div>
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

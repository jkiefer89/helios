import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { AIResult, DataStatusResponse, ModelSummary, ReportResponse, ReportSnapshot, ReportSnapshotStorage, SignalJournalEntry, TickerSummary } from "../api/types";
import { AICopilotPanel, reportCopilotActions } from "../components/ai/AICopilotPanel";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
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
  // No first-ticker fallback: report targets are operator-chosen, never a silent default.
  const defaultTarget = selectedModel ? `model:${selectedModel}` : selectedInstrument ? `instrument:${selectedInstrument}` : "";
  const [target, setTarget] = useState(defaultTarget);
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<ReportResponse>({ failureMessage: "Report build failed." });
  const [journalEntries, setJournalEntries] = useState<SignalJournalEntry[]>([]);
  const [journalError, setJournalError] = useState("");
  const [snapshots, setSnapshots] = useState<ReportSnapshot[]>([]);
  const [snapshotError, setSnapshotError] = useState("");
  const [savingSnapshot, setSavingSnapshot] = useState(false);
  const [aiNarrative, setAiNarrative] = useState("");
  // Default OFF: saving a report must never invoke the cloud provider
  // without explicit intent (review finding). The choice persists.
  const [includeAiNarrative, setIncludeAiNarrative] = useState<boolean>(() => {
    try {
      return localStorage.getItem("helios_report_ai") === "on";
    } catch {
      return false;
    }
  });
  const toggleAiNarrative = (checked: boolean) => {
    setIncludeAiNarrative(checked);
    try {
      localStorage.setItem("helios_report_ai", checked ? "on" : "off");
    } catch {
      // best-effort persistence
    }
  };
  const [preparedFor, setPreparedFor] = useState("");
  const [preparedBy, setPreparedBy] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [reportPurpose, setReportPurpose] = useState("advisor_review");
  const [snapshotStorage, setSnapshotStorage] = useState<ReportSnapshotStorage | null>(null);
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const build = useCallback((requestedTarget: string) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    setSnapshotError("");
    setAiNarrative("");
    if (kind === "model") onSelectModel(id);
    else onSelectInstrument(id);
    void load(requestedTarget, () => kind === "model" ? api.reportModel(id) : api.reportInstrument(id));
  }, [load, onSelectInstrument, onSelectModel]);

  useEffect(() => {
    if (!defaultTarget || isCurrentTarget(defaultTarget)) return;
    setTarget(defaultTarget);
    build(defaultTarget);
  }, [build, defaultTarget, isCurrentTarget]);

  const loadSnapshots = useCallback(async () => {
    try {
      const history = await api.reportSnapshots();
      setSnapshots(history.snapshots);
      setSnapshotStorage(history.storage);
      setSnapshotError(history.warning || "");
    } catch (err) {
      setSnapshots([]);
      setSnapshotError(err instanceof Error ? err.message : "Report history unavailable.");
    }
  }, []);

  useEffect(() => {
    void loadSnapshots();
  }, [loadSnapshots]);

  const loadJournal = useCallback(() => {
    let cancelled = false;
    api.signalJournal()
      .then((journal) => {
        if (!cancelled) {
          setJournalEntries(journal.entries);
          setJournalError("");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setJournalEntries([]);
          setJournalError(err instanceof Error ? err.message : "Signal Journal unavailable.");
        }
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => loadJournal(), [loadJournal, payload]);

  const saveSnapshot = async () => {
    const [kind, id] = target.split(":");
    if (!payload || !kind || !id) return;
    setSavingSnapshot(true);
    setSnapshotError("");
    try {
      const saved = await api.saveReportSnapshot({
        kind,
        id,
        ai_narrative: aiNarrative || undefined,
        include_ai_narrative: includeAiNarrative,
        prepared_for: preparedFor || undefined,
        prepared_by: preparedBy || undefined,
        reviewer: reviewer || undefined,
        report_purpose: reportPurpose,
      });
      setSnapshots((current) => [saved.snapshot, ...current.filter((row) => row.id !== saved.snapshot.id)]);
      setSnapshotStorage(saved.storage);
    } catch (err) {
      setSnapshotError(err instanceof Error ? err.message : "Report snapshot could not be saved.");
    } finally {
      setSavingSnapshot(false);
    }
  };

  return (
    <div className="view-stack report-view">
      <header className="view-head no-print">
        <div><div className="section-label">Analysis-Only Report</div><h1>Institutional Report System</h1><p>Advisor/client-ready reports with saved history, versioning, audit trails, disclosure blocks, and print/PDF layouts.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); build(target || defaultTarget); }}>
          <label>Target<TerminalSelect ariaLabel="Report target" value={target} onChange={setTarget} options={options} /></label>
          <label className="check"><input type="checkbox" checked={includeAiNarrative} onChange={(event) => toggleAiNarrative(event.target.checked)} /> Generate AI narrative on save (calls the cloud provider)</label>
          <button type="submit">Build preview</button>
          <button type="button" onClick={() => void saveSnapshot()} disabled={!payload || savingSnapshot}>
            {savingSnapshot ? "Saving..." : "Save snapshot"}
          </button>
          <button type="button" onClick={() => window.print()}>{payload?.eligible_for_real_research ? "Print / PDF layout" : "Print preview"}</button>
        </form>
      </header>
      <Panel title="Report Package Controls" meta="local saved history and versioning" className="no-print report-package-controls">
        <div className="report-control-grid">
          <label>Prepared for<input value={preparedFor} onChange={(event) => setPreparedFor(event.target.value)} placeholder="Investment committee or client household" /></label>
          <label>Prepared by<input value={preparedBy} onChange={(event) => setPreparedBy(event.target.value)} placeholder="Advisor or review desk" /></label>
          <label>Reviewer<input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="Required approver" /></label>
          <label>Purpose<TerminalSelect ariaLabel="Report purpose" value={reportPurpose} onChange={setReportPurpose} options={[
            { value: "advisor_review", label: "Advisor review" },
            { value: "client_review", label: "Client review" },
            { value: "investment_committee", label: "Investment committee" },
          ]} /></label>
        </div>
      </Panel>
      {error && <div className="notice danger" role="alert">{error}</div>}
      {snapshotError && <div className="notice warning no-print" role="alert">{snapshotError} <button type="button" onClick={() => void loadSnapshots()}>Retry history</button></div>}
      <SignalJournalPanel entries={journalEntries} error={journalError} onRetry={() => { loadJournal(); }} />
      <ReportHistoryPanel snapshots={snapshots} storage={snapshotStorage} />
      {isLoading ? (
        <div className="loading" role="status">Building the analysis-only report preview...</div>
      ) : !payload ? (
        <EmptyState title="Select a report target" body="Choose an instrument or model to build an analysis-only report." />
      ) : (
        <ReportSheet payload={payload} dataStatus={dataStatus} onAiNarrative={setAiNarrative} />
      )}
    </div>
  );
}

function ReportHistoryPanel({ snapshots, storage }: { snapshots: ReportSnapshot[]; storage: ReportSnapshotStorage | null }) {
  return (
    <Panel title="Report History" meta={`${snapshots.length} saved · ${storageLabel(storage)}`} className="no-print report-history-panel">
      {snapshots.length === 0 ? (
        <EmptyState title="No saved report snapshots" body="Build a report and save a snapshot to preserve the evidence pack with provenance." />
      ) : (
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable saved report snapshots">
          <table className="terminal-data-table report-history-data-table" aria-label="Saved report snapshots">
            <thead><tr>
              <th scope="col">Saved</th><th scope="col">Report Version</th><th scope="col">Package</th><th scope="col">Research Status</th><th scope="col">Source</th><th scope="col">Input Range</th><th scope="col">Audit Trail</th><th scope="col">Exports</th>
            </tr></thead>
            <tbody>{snapshots.slice(0, 12).map((snapshot) => (
              <tr key={snapshot.id}>
                <td>{fmtTimestamp(snapshot.created_at)}</td>
                <th scope="row"><strong>{snapshot.version_label || "v1"} · {snapshot.title}</strong><small>{snapshot.prepared_for || snapshot.target_name} · {snapshot.target_kind} · {snapshot.target_id}</small></th>
                <td>{titleCase(snapshot.report_package || "report snapshot")}<small>{titleCase(snapshot.report_purpose || "advisor review")}</small></td>
                <td><strong>{snapshot.eligible_for_real_research ? "Research ready" : "Not eligible"}</strong><small>{snapshot.display_label || titleCase(snapshot.data_mode)}</small></td>
                <td>{snapshot.source || "unknown"}<small>{formatSourceCounts(snapshot.source_counts)}</small></td>
                <td>{snapshot.first_date || "—"} → {snapshot.last_date || "—"}<small>{snapshot.row_count} rows</small></td>
                <td>{snapshot.audit_trail?.length || 0} events<small>{snapshot.ai_narrative_included ? "AI included" : snapshot.ai_narrative_status || "AI not included"}</small></td>
                <td className="report-export-links">
                  <a href={snapshot.html_url} target="_blank" rel="noreferrer">HTML Snapshot</a>
                  <a href={snapshot.pdf_url}>PDF Export</a>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SignalJournalPanel({ entries, error, onRetry }: { entries: SignalJournalEntry[]; error: string; onRetry: () => void }) {
  return (
    <Panel title="Signal Journal" meta="Paper tracking only" className="no-print signal-journal-panel">
      {error ? (
        <div className="notice warning" role="alert">{error} <button type="button" onClick={onRetry}>Retry journal</button></div>
      ) : entries.length === 0 ? (
        <EmptyState title="No signals logged yet" body="Run an instrument or model analysis to start tracking forward paper results." />
      ) : (
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable Signal Journal paper performance">
          <table className="terminal-data-table report-signal-journal-data-table">
            <thead><tr><th scope="col">Date</th><th scope="col">Target</th><th scope="col">Action</th><th scope="col">Score</th><th scope="col">Input Range</th><th scope="col">Benchmark</th><th scope="col">Forward Result</th></tr></thead>
            <tbody>{entries.slice(0, 12).map((entry) => (
              <tr key={entry.id}>
                <td>{fmtTimestamp(entry.created_at)}</td>
                <th scope="row"><strong>{entry.target_name}</strong><small>{entry.target_kind} · {entry.target_id}</small></th>
                <td>{entry.action_label}</td>
                <td>{fmtNumber(entry.score, 2)}</td>
                <td>{entry.input_start_date} → {entry.input_end_date}<small>{entry.input_rows} rows · {entry.horizon_days}d</small></td>
                <td>{entry.benchmark || "—"}</td>
                <td>{entry.forward_status === "measured" ? fmtPct(entry.forward_result_pct) : "Pending"}<small>{entry.alpha_pct == null ? "" : `Alpha ${fmtPct(entry.alpha_pct)}`}</small></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ReportSheet({ payload, dataStatus, onAiNarrative }: { payload: ReportResponse; dataStatus: DataStatusResponse | null; onAiNarrative: (value: string) => void }) {
  const previewLocked = !payload.eligible_for_real_research;
  const sections = previewLocked ? maskPreviewSections(payload.sections) : payload.sections;
  const title = reportTitle(payload);
  const copilotPayload = useMemo<Record<string, unknown>>(() => ({
    report: payload,
    data_status: dataStatus,
    preview_locked: previewLocked,
    title,
  }), [dataStatus, payload, previewLocked, title]);
  return (
    <article className="report-sheet">
      <div className="report-masthead" aria-hidden="true">
        <span className="report-masthead__mark" />
        <b>Helios <i>Pro</i></b>
        <em>Analysis-Only Research Report</em>
      </div>
      <header>
        <div><div className="section-label">Helios Analysis-Only Report Preview</div><h1>{title}</h1><p>{fmtTimestamp(payload.timestamp)}</p></div>
        <span className="badge">{payload.kind}</span>
      </header>
      <DataQualityBanner payload={payload} compact />
      <InstitutionalDisclosurePanel />
      <PersistenceSnapshot dataStatus={dataStatus} />
      <AICopilotPanel
        contextLabel={title}
        payload={copilotPayload}
        dataMode={payload.data_mode}
        actions={reportCopilotActions}
        onResult={(result) => onAiNarrative(aiResultToNarrative(result))}
      />
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

function InstitutionalDisclosurePanel() {
  return (
    <Panel title="Disclosure Blocks" meta="Analysis-only report controls" className="report-disclosure-panel">
      <div className="report-disclosure-grid">
        <article><strong>Analysis-only use</strong><span>Helios reports evidence and caveats; it does not provide investment advice.</span></article>
        <article><strong>No trade execution</strong><span>No report, score, or AI narrative routes orders or rebalances accounts.</span></article>
        <article><strong>No return guarantee</strong><span>Forecasts and historical evidence are estimates, not promises.</span></article>
        <article><strong>Print / PDF layout</strong><span>Saved snapshots freeze source, date range, row count, version, audit trail, and disclosures.</span></article>
      </div>
    </Panel>
  );
}

function aiResultToNarrative(result: AIResult) {
  const parts = [
    result.summary,
    result.advisor_language,
    result.data_quality_statement,
    result.key_points.length ? `Key points:\n${result.key_points.map((row) => `- ${row}`).join("\n")}` : "",
    result.risks.length ? `Risks:\n${result.risks.map((row) => `- ${row}`).join("\n")}` : "",
    result.compliance_caveats.length ? `Compliance caveats:\n${result.compliance_caveats.map((row) => `- ${row}`).join("\n")}` : "",
  ];
  return parts.filter(Boolean).join("\n\n");
}

function formatSourceCounts(sourceCounts: Record<string, number>) {
  const rows = Object.entries(sourceCounts || {});
  if (!rows.length) return "";
  return rows.map(([source, count]) => `${source}: ${count}`).join(" · ");
}

function storageLabel(storage: ReportSnapshotStorage | null) {
  if (!storage) return "checking storage";
  if (!storage.durable) return "history unavailable";
  return storage.encrypted_at_rest ? "Encrypted local history" : "Local history";
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
        <div><dt>At-Rest Storage</dt><dd>{dataStatus.database.encryption?.enabled ? "Encrypted" : "Plaintext"}</dd></div>
        <div><dt>Missing Holdings</dt><dd>{dataStatus.missing_data.missing_tickers.join(", ") || "None"}</dd></div>
      </dl>
      {dataStatus.warnings.length > 0 && <div className="warning-list compact-list">{dataStatus.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
    </Panel>
  );
}

function ReportValue({ value, labelKey = "" }: { value: unknown; labelKey?: string }) {
  if (value == null) return <span className="muted">—</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">None</span>;
    const tableKeys = uniformObjectKeys(value);
    if (tableKeys) {
      // Sentence-like columns (40+ chars) get a wider track so they wrap to a
      // line or two instead of ballooning every row in the grid.
      const widths = tableKeys.map((key) => {
        const longest = Math.max(...value.map((item) => String((item as Record<string, unknown>)[key] ?? "").length));
        return longest > 40 ? "minmax(220px, 2.2fr)" : "minmax(96px, 1fr)";
      });
      const columns = { gridTemplateColumns: widths.join(" ") };
      return (
        <div className="report-array-table" role="table" aria-label={`${titleCase(labelKey || "items")} table`}>
          <div className="report-array-table__head" role="row" style={columns}>
            {tableKeys.map((key) => <span role="columnheader" key={key}>{titleCase(key)}</span>)}
          </div>
          {value.map((item, index) => (
            <div className="report-array-table__row" role="row" key={index} style={columns}>
              {tableKeys.map((key) => <span role="cell" key={key}><ReportValue value={(item as Record<string, unknown>)[key]} labelKey={key} /></span>)}
            </div>
          ))}
        </div>
      );
    }
    return <ul className="report-list">{value.map((item, index) => <li key={index}><ReportValue value={item} labelKey={labelKey} /></li>)}</ul>;
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
  const mode = String(payload.data_mode || payload.data_provenance?.data_mode || "").toLowerCase();
  const label = String(payload.display_label || payload.data_provenance?.display_label || "").toLowerCase();
  if (mode === "invalid_for_research" || label.includes("blocked")) return "Data Quality Blocked — Upload Real History";
  if (mode === "demo" || mode === "sample" || mode === "simulated") {
    return `Not Real Market Evidence — ${payload.title}`;
  }
  if (mode === "mixed") return `Mixed Data — Research Ineligible — ${payload.title}`;
  return payload.title;
}

function looksLikeTimestamp(value: string) {
  return /^\d{4}-\d{2}-\d{2}T/.test(value) || /^\d{4}-\d{2}-\d{2}\s+\d{2}:/.test(value);
}

// Arrays of three-plus uniform flat objects (e.g. per-ticker liquidity rows)
// render as a compact table; anything nested falls back to the recursive
// renderer so no payload shape is ever dropped.
function uniformObjectKeys(rows: unknown[]): string[] | null {
  if (rows.length < 3) return null;
  const keys: string[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return null;
    for (const [key, nested] of Object.entries(row as Record<string, unknown>)) {
      if (nested !== null && typeof nested === "object") return null;
      // Prose-heavy fields (advisor language sentences) read badly in a grid
      // cell; those arrays fall back to the stacked recursive renderer.
      if (typeof nested === "string" && nested.length > 80) return null;
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys.length >= 2 && keys.length <= 10 ? keys : null;
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

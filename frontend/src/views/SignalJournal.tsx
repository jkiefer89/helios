import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { SignalJournalBenchmarkComparison, SignalJournalEntry, SignalJournalModelEvidence, SignalJournalResponse } from "../api/types";
import { ChartSummary, LineChart } from "../components/charts/Charts";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtPct, fmtTimestamp, sourceSummary, titleCase } from "../utils/format";

export function SignalJournal() {
  const [payload, setPayload] = useState<SignalJournalResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setPayload(await api.signalJournal());
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : "Signal Journal unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const pending = useMemo(() => payload?.entries.filter((entry) => entry.forward_status !== "measured") || [], [payload]);
  const measured = useMemo(() => payload?.entries.filter((entry) => entry.forward_status === "measured") || [], [payload]);
  const driftLabels = useMemo(() => payload?.drift.map((point) => point.input_end_date || String(point.index)) || [], [payload]);
  const driftSeries = useMemo(() => payload ? [
    { label: "Signal score", values: payload.drift.map((point) => point.score), tone: "info" },
    { label: "Alpha", values: payload.drift.map((point) => point.alpha_pct), tone: "positive" },
    { label: "Hit rate", values: payload.drift.map((point) => point.cumulative_hit_rate_pct), tone: "warning" },
  ] : [], [payload]);

  if (loading && !payload) return <div className="loading">Loading Signal Journal...</div>;

  return (
    <div className="view-stack signal-journal-view">
      <header className="view-head">
        <div>
          <div className="section-label">Signal Journal</div>
          <h1>Paper performance tracking</h1>
          <p>Every deterministic Helios signal is tracked with input range, action, benchmark, pending or measured forward result, and analysis-only caveats.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <button type="submit">{loading ? "Refreshing..." : "Refresh journal"}</button>
        </form>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}
      {!payload ? (
        <EmptyState title="Signal Journal unavailable" body="The backend did not return paper-performance journal data." />
      ) : payload.entries.length === 0 ? (
        <EmptyState title="No signal history yet" body="Run instrument or model analysis to start logging paper results. Helios stores compact audit facts, not raw price histories." />
      ) : (
        <>
          <section className="dashboard-grid three">
            <Panel title="Hit Rate" meta={`${payload.summary.measured_count} measured`}>
              <div className="metric-grid">
                <StatTile label="Paper hit rate" value={fmtPct(payload.summary.hit_rate_pct)} tone={toneForPositive(payload.summary.hit_rate_pct)} />
                <StatTile label="Avg alpha" value={fmtPct(payload.summary.avg_alpha_pct)} tone={toneForSigned(payload.summary.avg_alpha_pct)} />
                <StatTile label="Avg forward" value={fmtPct(payload.summary.avg_forward_result_pct)} tone={toneForSigned(payload.summary.avg_forward_result_pct)} />
                <StatTile label="Avg score" value={fmtNumber(payload.summary.avg_score, 2)} />
              </div>
            </Panel>
            <Panel title="Pending Forward Results" meta={`${payload.summary.pending_count} pending`}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Signals" value={fmtNumber(payload.summary.total_count, 0)} />
                <StatTile label="Measured" value={fmtNumber(payload.summary.measured_count, 0)} tone="positive" />
                <StatTile label="Pending" value={fmtNumber(payload.summary.pending_count, 0)} tone={payload.summary.pending_count ? "warning" : "positive"} />
                <StatTile label="Research-ready" value={fmtNumber(payload.summary.research_ready_count, 0)} />
              </div>
            </Panel>
            <Panel title="Journal Coverage" meta="targets">
              <ChartSummary items={[
                { label: "Models", value: fmtNumber(payload.summary.model_count, 0), tone: "info" },
                { label: "Instruments", value: fmtNumber(payload.summary.instrument_count, 0), tone: "info" },
                { label: "Benchmarks", value: fmtNumber(payload.benchmark_comparison.length, 0), tone: "neutral" },
                { label: "Measured rows", value: fmtNumber(measured.length, 0), tone: "positive" },
              ]} />
            </Panel>
          </section>

          <Panel title="Drift Over Time" meta="score, alpha and hit-rate history">
            <LineChart labels={driftLabels} series={driftSeries} height={220} />
          </Panel>

          <section className="dashboard-grid">
            <BenchmarkComparison rows={payload.benchmark_comparison} />
            <ModelEvidence rows={payload.model_evidence} />
          </section>

          <section className="dashboard-grid">
            <PendingResults rows={pending} />
            <SignalHistory rows={payload.entries} />
          </section>

          <Panel title="Methodology" meta="analysis-only">
            <dl className="report-dl">
              <div><dt>Tracking</dt><dd>Paper tracking only. No brokerage execution, orders, or return guarantees.</dd></div>
              <div><dt>Hit Rate Basis</dt><dd>{String(payload.methodology.hit_rate_basis || "Measured paper signals are evaluated against action intent and benchmark evidence when available.")}</dd></div>
              <div><dt>Forward Results</dt><dd>{String(payload.methodology.forward_results || "Pending results resolve only when later persisted market data covers the signal horizon.")}</dd></div>
            </dl>
          </Panel>
          <p className="report-disclaimer">{payload.disclaimer}</p>
        </>
      )}
    </div>
  );
}

function BenchmarkComparison({ rows }: { rows: SignalJournalBenchmarkComparison[] }) {
  return (
    <Panel title="Benchmark Comparison" meta={`${rows.length} benchmarks`}>
      {rows.length === 0 ? (
        <EmptyState title="No measured benchmark rows" body="Benchmark comparison appears after measured forward results include benchmark data." />
      ) : (
        <div className="terminal-table journal-benchmark-table" tabIndex={0} aria-label="Signal Journal benchmark comparison">
          <div className="terminal-table__head"><span>Benchmark</span><span>Measured</span><span>Forward</span><span>Benchmark</span><span>Alpha</span><span>Hit Rate</span></div>
          {rows.map((row) => (
            <div className="table-row" key={row.benchmark}>
              <span><strong>{row.benchmark}</strong></span>
              <span>{fmtNumber(row.measured_count, 0)}</span>
              <span>{fmtPct(row.avg_forward_result_pct)}</span>
              <span>{fmtPct(row.avg_benchmark_result_pct)}</span>
              <span className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</span>
              <span>{fmtPct(row.hit_rate_pct)}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function ModelEvidence({ rows }: { rows: SignalJournalModelEvidence[] }) {
  return (
    <Panel title="Model-by-Model Evidence" meta={`${rows.length} models`}>
      {rows.length === 0 ? (
        <EmptyState title="No model-level signals yet" body="Run model analysis to build model-by-model evidence and forward result history." />
      ) : (
        <div className="terminal-table journal-model-table" tabIndex={0} aria-label="Model-by-model signal journal evidence">
          <div className="terminal-table__head"><span>Model</span><span>Signals</span><span>Latest</span><span>Hit Rate</span><span>Alpha</span><span>Sources</span></div>
          {rows.map((row) => (
            <div className="table-row" key={row.target_id}>
              <span><strong>{row.target_name}</strong><small>{row.target_id} · {row.data_modes.map(titleCase).join(", ")}</small></span>
              <span>{row.measured_count}/{row.signal_count}<small>{row.pending_count} pending</small></span>
              <span>{row.latest_action_label}<small>{row.latest_input_end_date} · score {fmtNumber(row.latest_score, 2)}</small></span>
              <span>{fmtPct(row.hit_rate_pct)}</span>
              <span className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</span>
              <span>{sourceSummary(row.source_counts)}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function PendingResults({ rows }: { rows: SignalJournalEntry[] }) {
  return (
    <Panel title="Pending Forward Results" meta={`${rows.length} unresolved`}>
      {rows.length === 0 ? (
        <EmptyState title="No pending paper results" body="All logged signals currently have measured forward results." />
      ) : (
        <div className="terminal-table journal-pending-table" tabIndex={0} aria-label="Pending signal forward results">
          <div className="terminal-table__head"><span>Target</span><span>Action</span><span>Input Range</span><span>Horizon</span><span>Benchmark</span></div>
          {rows.slice(0, 12).map((entry) => (
            <div className="table-row" key={entry.id}>
              <span><strong>{entry.target_name}</strong><small>{entry.target_kind} · {entry.target_id}</small></span>
              <span>{entry.action_label}<small>score {fmtNumber(entry.score, 2)}</small></span>
              <span>{entry.input_start_date} to {entry.input_end_date}<small>{entry.input_rows} rows</small></span>
              <span>{entry.horizon_days}d</span>
              <span>{entry.benchmark || "none"}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SignalHistory({ rows }: { rows: SignalJournalEntry[] }) {
  return (
    <Panel title="Signal History" meta={`${rows.length} logged`}>
      <div className="terminal-table journal-history-table" tabIndex={0} aria-label="Signal history">
        <div className="terminal-table__head"><span>Date</span><span>Target</span><span>Action</span><span>Score</span><span>Benchmark</span><span>Forward</span><span>Alpha</span></div>
        {rows.slice(0, 40).map((entry) => (
          <div className="table-row" key={entry.id}>
            <span>{fmtTimestamp(entry.created_at)}</span>
            <span><strong>{entry.target_name}</strong><small>{entry.target_kind} · {entry.input_end_date}</small></span>
            <span>{entry.action_label}</span>
            <span>{fmtNumber(entry.score, 2)}</span>
            <span>{entry.benchmark || "none"}</span>
            <span>{entry.forward_status === "measured" ? fmtPct(entry.forward_result_pct) : "Pending"}<small>{entry.forward_end_date || ""}</small></span>
            <span className={toneClass(entry.alpha_pct)}>{fmtPct(entry.alpha_pct)}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function toneForSigned(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value >= 0 ? "positive" : "negative" : "neutral";
}

function toneForPositive(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 50 ? "positive" : "warning";
}

function toneClass(value: unknown) {
  return `tone-${toneForSigned(value)}`;
}

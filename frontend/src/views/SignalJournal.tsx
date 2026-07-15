import { useCallback, useEffect, useMemo } from "react";
import { api } from "../api/client";
import type { SignalJournalBenchmarkComparison, SignalJournalEntry, SignalJournalModelEvidence, SignalJournalResponse } from "../api/types";
import { ChartSummary, LineChart } from "../components/charts/Charts";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { RequestStatus } from "../components/states/RequestStatus";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtNumber, fmtPct, fmtTimestamp, sourceSummary, titleCase } from "../utils/format";

export function SignalJournal() {
  const {
    payload, failure, isLoading: loading, load, retry, staleResult,
  } = useViewFetch<SignalJournalResponse>({
    failureMessage: "Signal Journal unavailable.",
    keepPayloadWhileLoading: true,
  });

  const refresh = useCallback(
    () => load("signal-journal", api.signalJournal),
    [load],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
          <p>Deliberately recorded Helios signals are tracked with input range, action, benchmark, pending or measured forward result, and analysis-only caveats.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void refresh(); }}>
          <button type="submit">{loading ? "Refreshing..." : "Refresh journal"}</button>
        </form>
      </header>

      <RequestStatus failure={failure} stale={staleResult} onRetry={retry} />
      {!payload ? (
        <EmptyState title="Signal Journal unavailable" body="The backend did not return paper-performance journal data." />
      ) : payload.entries.length === 0 ? (
        <EmptyState title="No signal history yet" body="Open Analysis and choose Record Helios signal to begin prospective paper measurement. Viewing analysis never writes to the journal." />
      ) : (
        <>
          <section className="dashboard-grid three">
            <Panel title="Directional Evidence" meta={`${payload.summary.measured_count} benchmark-measured`}>
              <div className="metric-grid">
                <StatTile label="Paper hit rate" value={fmtPct(payload.summary.hit_rate_pct)} tone={toneForPositive(payload.summary.hit_rate_pct)} />
                <StatTile label="HOLD preservation" value={fmtPct(payload.summary.hold_preservation_rate_pct)} tone={toneForPositive(payload.summary.hold_preservation_rate_pct)} />
                <StatTile label="Avg alpha (gross)" value={fmtPct(payload.summary.avg_alpha_pct)} tone={toneForSigned(payload.summary.avg_alpha_pct)} />
                <StatTile
                  label="Alpha net of default costs"
                  value={fmtPct(payload.summary.avg_alpha_after_default_costs_pct)}
                  tone={toneForSigned(payload.summary.avg_alpha_after_default_costs_pct)}
                />
                <StatTile label="Avg forward" value={fmtPct(payload.summary.avg_forward_result_pct)} tone={toneForSigned(payload.summary.avg_forward_result_pct)} />
                <StatTile label="Avg score" value={fmtNumber(payload.summary.avg_score, 2)} />
              </div>
            </Panel>
            <Panel title="Pending Forward Results" meta={`${payload.summary.pending_count} pending`}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Signals" value={fmtNumber(payload.summary.total_count, 0)} />
                <StatTile label="Completed outcomes" value={fmtNumber(payload.summary.outcome_count, 0)} tone="positive" />
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
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable Signal Journal benchmark comparison">
          <table className="terminal-data-table journal-benchmark-data-table">
            <thead><tr><th scope="col">Benchmark</th><th scope="col">Measured</th><th scope="col">Forward</th><th scope="col">Benchmark</th><th scope="col">Alpha</th><th scope="col">Hit Rate</th></tr></thead>
            <tbody>{rows.map((row) => (
              <tr key={row.benchmark}>
                <th scope="row"><strong>{row.benchmark}</strong></th>
                <td>{fmtNumber(row.measured_count, 0)}</td>
                <td>{fmtPct(row.avg_forward_result_pct)}</td>
                <td>{fmtPct(row.avg_benchmark_result_pct)}</td>
                <td className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</td>
                <td>{fmtPct(row.hit_rate_pct)}</td>
              </tr>
            ))}</tbody>
          </table>
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
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable model-by-model signal journal evidence">
          <table className="terminal-data-table journal-model-data-table">
            <thead><tr><th scope="col">Model</th><th scope="col">Signals</th><th scope="col">Latest</th><th scope="col">Hit Rate</th><th scope="col">Alpha</th><th scope="col">Sources</th></tr></thead>
            <tbody>{rows.map((row) => (
              <tr key={row.target_id}>
                <th scope="row"><strong>{row.target_name}</strong><small>{row.target_id} · {row.data_modes.map(titleCase).join(", ")}</small></th>
                <td>{row.measured_count}/{row.signal_count}<small>{row.pending_count} pending</small></td>
                <td>{row.latest_action_label}<small>{row.latest_input_end_date} · score {fmtNumber(row.latest_score, 2)}</small></td>
                <td>{fmtPct(row.hit_rate_pct)}</td>
                <td className={toneClass(row.avg_alpha_pct)}>{fmtPct(row.avg_alpha_pct)}</td>
                <td>{sourceSummary(row.source_counts)}</td>
              </tr>
            ))}</tbody>
          </table>
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
        <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable pending signal forward results">
          <table className="terminal-data-table journal-pending-data-table">
            <thead><tr><th scope="col">Target</th><th scope="col">Action</th><th scope="col">Input Range</th><th scope="col">Horizon</th><th scope="col">Benchmark</th></tr></thead>
            <tbody>{rows.slice(0, 12).map((entry) => (
              <tr key={entry.id}>
                <th scope="row"><strong>{entry.target_name}</strong><small>{entry.target_kind} · {entry.target_id}</small></th>
                <td>{entry.action_label}<small>score {fmtNumber(entry.score, 2)}</small></td>
                <td>{entry.input_start_date} to {entry.input_end_date}<small>{entry.input_rows} rows</small></td>
                <td>{entry.horizon_days}d</td>
                <td>{entry.benchmark || "none"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function SignalHistory({ rows }: { rows: SignalJournalEntry[] }) {
  return (
    <Panel title="Signal History" meta={`${rows.length} logged`}>
      <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable signal history">
        <table className="terminal-data-table journal-history-data-table">
          <thead><tr><th scope="col">Date</th><th scope="col">Target</th><th scope="col">Action</th><th scope="col">Score</th><th scope="col">Benchmark</th><th scope="col">Forward</th><th scope="col">Alpha</th></tr></thead>
          <tbody>{rows.slice(0, 40).map((entry) => (
            <tr key={entry.id}>
              <td>{fmtTimestamp(entry.created_at)}</td>
              <th scope="row"><strong>{entry.target_name}</strong><small>{entry.target_kind} · {entry.input_end_date}</small></th>
              <td>{entry.action_label}</td>
              <td>{fmtNumber(entry.score, 2)}</td>
              <td>{entry.benchmark || "none"}</td>
              <td>{entry.forward_status === "measured" ? fmtPct(entry.forward_result_pct) : "Pending"}<small>{entry.forward_end_date || ""}</small></td>
              <td className={toneClass(entry.alpha_pct)}>{fmtPct(entry.alpha_pct)}</td>
            </tr>
          ))}</tbody>
        </table>
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

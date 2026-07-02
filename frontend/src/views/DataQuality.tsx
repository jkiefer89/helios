import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DataQualityAlert, DataQualityIssue, DataQualityResponse } from "../api/types";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtTimestamp, titleCase } from "../utils/format";

export function DataQuality() {
  const [payload, setPayload] = useState<DataQualityResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setPayload(await api.dataQuality());
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : "Data quality dashboard unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (loading && !payload) return <div className="loading">Loading institutional data quality checks...</div>;

  return (
    <div className="view-stack data-quality-view">
      <header className="view-head">
        <div>
          <div className="section-label">Institutional Data Quality</div>
          <h1>Research readiness dashboard</h1>
          <p>Dedicated review of stale symbols, missing data, short histories, source conflicts, refresh failures, and model coverage gaps.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <button type="submit">{loading ? "Refreshing..." : "Refresh checks"}</button>
        </form>
      </header>

      {error && <div className="notice danger" role="alert">{error}</div>}
      {payload ? (
        <>
          <section className="dashboard-grid three">
            <Panel title="Research-ready Status" meta={fmtTimestamp(payload.generated_at)}>
              <div className={`readiness-verdict ${payload.research_ready ? "ready" : "blocked"}`}>
                <strong>{payload.research_ready ? "Research-ready" : "Not research-ready"}</strong>
                <span>{payload.summary.blocker_count} blockers · {payload.summary.warning_count} warnings</span>
              </div>
              <div className="metric-grid">
                <StatTile label="Ready rows" value={fmtNumber(payload.summary.research_ready_count, 0)} tone={payload.summary.research_ready_count ? "positive" : "warning"} />
                <StatTile label="Issues" value={fmtNumber(payload.summary.issue_count, 0)} tone={payload.summary.issue_count ? "warning" : "positive"} />
                <StatTile label="Symbols" value={fmtNumber(payload.summary.symbol_count, 0)} />
                <StatTile label="Models" value={fmtNumber(payload.summary.model_count, 0)} />
              </div>
            </Panel>
            <Panel title="Quality Thresholds" meta="institutional defaults">
              <dl className="report-dl">
                <div><dt>Stale Symbols</dt><dd>{payload.thresholds.stale_days} calendar days</dd></div>
                <div><dt>Research Minimum</dt><dd>{payload.thresholds.min_research_rows} rows</dd></div>
                <div><dt>Institutional History</dt><dd>{payload.thresholds.institutional_history_rows} rows</dd></div>
                <div><dt>Threshold Source</dt><dd>{payload.threshold_config.source}</dd></div>
              </dl>
            </Panel>
            <Panel title="Issue Mix" meta={`${payload.issues.length} total`}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Stale Symbols" value={fmtNumber(payload.summary.stale_symbol_count, 0)} tone={payload.summary.stale_symbol_count ? "warning" : "positive"} />
                <StatTile label="Missing Data" value={fmtNumber(payload.summary.missing_symbol_count, 0)} tone={payload.summary.missing_symbol_count ? "warning" : "positive"} />
                <StatTile label="Refresh Failures" value={fmtNumber(payload.summary.refresh_failure_count, 0)} tone={payload.summary.refresh_failure_count ? "warning" : "positive"} />
                <StatTile label="Observability Gaps" value={fmtNumber(payload.summary.refresh_observability_gap_count, 0)} tone={payload.summary.refresh_observability_gap_count ? "warning" : "positive"} />
                <StatTile label="Coverage Gaps" value={fmtNumber(payload.summary.coverage_gap_count, 0)} tone={payload.summary.coverage_gap_count ? "warning" : "positive"} />
              </div>
            </Panel>
          </section>

          <Panel title="Alert Center" meta={payload.alerts.tracking_available ? `${payload.alerts.summary.notification_count} notifications` : "tracking unavailable"}>
            {!payload.alerts.tracking_available && (
              <div className="notice warning">{payload.alerts.warning || "SQLite alert tracking is unavailable; current checks are still shown below."}</div>
            )}
            <div className="metric-grid compact-metrics">
              <StatTile label="Active Alerts" value={fmtNumber(payload.alerts.summary.active_count, 0)} tone={payload.alerts.summary.active_count ? "warning" : "positive"} />
              <StatTile label="Blockers" value={fmtNumber(payload.alerts.summary.blocker_count, 0)} tone={payload.alerts.summary.blocker_count ? "negative" : "positive"} />
              <StatTile label="New / Reopened" value={fmtNumber(payload.alerts.summary.new_count, 0)} tone={payload.alerts.summary.new_count ? "warning" : "neutral"} />
              <StatTile label="Resolved Alerts" value={fmtNumber(payload.alerts.summary.resolved_count, 0)} tone={payload.alerts.summary.resolved_count ? "positive" : "neutral"} />
            </div>
            <section className="quality-alert-ledger">
              <AlertFeed title="Active Alerts" alerts={payload.alerts.active} empty="No active data quality alerts." />
              <AlertFeed title="Resolved Alerts" alerts={payload.alerts.resolved.slice(0, 6)} empty="No resolved alerts have been tracked yet." />
            </section>
          </Panel>

          <Panel title="Refresh Evidence" meta={`${payload.refresh_observability.observed_count} observed · ${payload.refresh_observability.gap_count} gaps`}>
            <dl className="report-dl">
              <div><dt>Required For</dt><dd>{payload.refresh_observability.requires_log_for_sources.join(", ") || "None"}</dd></div>
              <div><dt>Evidence Basis</dt><dd>{payload.refresh_observability.basis}</dd></div>
              <div><dt>Log Window</dt><dd>{fmtNumber(payload.refresh_observability.refresh_log_window, 0)} latest attempts</dd></div>
              <div><dt>Gaps</dt><dd>{fmtNumber(payload.refresh_observability.gap_count, 0)}</dd></div>
            </dl>
          </Panel>

          <section className="dashboard-grid">
            <IssuePanel title="Stale Symbols" rows={payload.stale_symbols.map((row) => ({
              target: row.symbol,
              detail: `${row.name} · ${row.days_stale ?? "?"} days stale · last ${row.last_date || "unknown"} · ${row.freshness_basis}`,
              next_step: row.next_step,
              severity: "warning",
            }))} />
            <IssuePanel title="Short Histories" rows={payload.short_histories.map((row) => ({
              target: row.symbol,
              detail: `${row.row_count} rows · ${row.first_date || "?"} to ${row.last_date || "?"}`,
              next_step: row.next_step,
              severity: row.row_count < payload.thresholds.min_research_rows ? "blocker" : "warning",
            }))} />
          </section>

          <section className="dashboard-grid">
            <IssuePanel title="Observability Gaps" rows={payload.refresh_observability_gaps.map((row) => ({
              target: row.symbol,
              detail: `${row.name} is live data without a persisted refresh attempt. Last price date ${row.last_date || "unknown"}.`,
              next_step: row.next_step,
              severity: "warning",
            }))} />
            <Panel title="Refresh Failures" meta={`${payload.refresh_failures.length} failed`}>
              {payload.refresh_failures.length === 0 ? (
                <EmptyState title="No refresh failures" body="Live provider refreshes have not reported failures in the stored log." />
              ) : (
                <div className="terminal-table quality-table" tabIndex={0} aria-label="Refresh failure table">
                  <div className="terminal-table__head"><span>Symbol</span><span>Attempted</span><span>Rows</span><span>Message</span></div>
                  {payload.refresh_failures.map((row) => (
                    <div className="table-row" key={`${row.symbol}-${row.attempted_at}`}>
                      <span><strong>{row.symbol}</strong><small>{row.source}</small></span>
                      <span>{fmtTimestamp(row.attempted_at)}</span>
                      <span>{fmtNumber(row.rows_added, 0)}</span>
                      <span>{row.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title="Coverage Gaps" meta={`${payload.coverage_gaps.length} models`}>
              {payload.coverage_gaps.length === 0 ? (
                <EmptyState title="No model coverage gaps" body="Every imported model has real history coverage for every holding." />
              ) : (
                <div className="terminal-table quality-table" tabIndex={0} aria-label="Coverage gaps table">
                  <div className="terminal-table__head"><span>Model</span><span>Coverage</span><span>State</span><span>Missing</span></div>
                  {payload.coverage_gaps.map((row) => (
                    <div className="table-row" key={row.model_id}>
                      <span><strong>{row.model_name}</strong><small>{row.model_id}</small></span>
                      <span>{row.real_coverage_count}/{row.n_holdings}</span>
                      <span>{row.coverage_state}</span>
                      <span>{row.missing_tickers.join(", ") || "None"}</span>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </section>

          <section className="dashboard-grid">
            <IssuePanel title="Source Conflicts" rows={payload.source_conflicts.map((row) => ({
              target: row.model_name,
              detail: Object.entries(row.source_counts).map(([source, count]) => `${source}: ${count}`).join(" · "),
              next_step: "Normalize each holding to eligible live/uploaded history before model-level research.",
              severity: "warning",
            }))} />
            <IssuePanel title="Missing Data" rows={payload.missing_data.map((row) => ({
              target: row.symbol,
              detail: `${row.model_name} requires this holding.`,
              next_step: "Fetch live history or upload an eligible adjusted price file.",
              severity: "blocker",
            }))} />
          </section>

          <Panel title="All Data Quality Issues" meta={`${payload.issues.length} checks`}>
            {payload.issues.length === 0 ? (
              <EmptyState title="No data quality issues" body="The current local workspace passes the configured data quality checks." />
            ) : (
              <div className="terminal-table quality-table" tabIndex={0} aria-label="All data quality issues">
                <div className="terminal-table__head"><span>Severity</span><span>Category</span><span>Target</span><span>Detail</span><span>Next step</span></div>
                {payload.issues.map((issue, index) => (
                  <div className="table-row" key={`${issue.category}-${issue.target}-${index}`}>
                    <span className={`quality-severity severity-${issue.severity}`}>{issue.severity}</span>
                    <span>{titleCase(issue.category)}</span>
                    <span><strong>{issue.target}</strong></span>
                    <span>{issue.detail}</span>
                    <span>{issue.next_step}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
          <p className="report-disclaimer">{payload.disclaimer}</p>
        </>
      ) : (
        <EmptyState title="Data quality unavailable" body="The backend did not return a data quality payload." />
      )}
    </div>
  );
}

function AlertFeed({ title, alerts, empty }: { title: string; alerts: DataQualityAlert[]; empty: string }) {
  return (
    <div className="quality-alert-feed">
      <div className="quality-alert-feed__head">
        <strong>{title}</strong>
        <span>{fmtNumber(alerts.length, 0)}</span>
      </div>
      {alerts.length === 0 ? (
        <EmptyState title={title} body={empty} />
      ) : (
        <div className="alert-terminal-list">
          {alerts.map((alert) => (
            <article key={alert.id} className={`alert-terminal-row severity-${alert.severity}`}>
              <b className={`quality-severity severity-${alert.severity}`}>{alert.severity}</b>
              <span>
                <strong>{alert.target}</strong>
                <small>{titleCase(alert.category)} · {alert.detail}</small>
                <small>{alert.next_step}</small>
              </span>
              <em>
                {titleCase(alert.notification_state)}
                <small>{alert.status === "resolved" ? fmtTimestamp(alert.resolved_at || alert.last_changed_at) : fmtTimestamp(alert.last_seen_at)}</small>
                <small>{fmtNumber(alert.occurrence_count, 0)} seen</small>
              </em>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function IssuePanel({ title, rows }: { title: string; rows: Array<Pick<DataQualityIssue, "target" | "detail" | "next_step" | "severity">> }) {
  return (
    <Panel title={title} meta={`${rows.length} flagged`}>
      {rows.length === 0 ? (
        <EmptyState title={`No ${title.toLowerCase()}`} body="No rows are currently flagged in this category." />
      ) : (
        <div className="quality-issue-list">
          {rows.map((row, index) => (
            <article key={`${row.target}-${index}`} className={`quality-issue-card severity-${row.severity}`}>
              <strong>{row.target}</strong>
              <p>{row.detail}</p>
              <span>{row.next_step}</span>
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}

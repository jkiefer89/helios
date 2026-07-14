import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  DataJobsResponse, DataQualityAlert, DataQualityIssue, DataQualityResponse,
  OperationsStatusResponse, ProvidersResponse,
} from "../api/types";
import { Panel, StatTile } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtNumber, fmtTimestamp, titleCase } from "../utils/format";

export function DataQuality() {
  const [payload, setPayload] = useState<DataQualityResponse | null>(null);
  const [jobs, setJobs] = useState<DataJobsResponse | null>(null);
  const [jobsError, setJobsError] = useState("");
  const [providers, setProviders] = useState<ProvidersResponse | null>(null);
  const [operations, setOperations] = useState<OperationsStatusResponse | null>(null);
  const [controlsError, setControlsError] = useState("");
  const [controlsBusy, setControlsBusy] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const loadInstitutionalControls = useCallback(async () => {
    const results = await Promise.allSettled([api.providers(), api.operationsStatus()]);
    const failures: string[] = [];
    if (results[0].status === "fulfilled") setProviders(results[0].value);
    else failures.push("provider registry");
    if (results[1].status === "fulfilled") setOperations(results[1].value);
    else failures.push("operations status");
    setControlsError(failures.length ? `Unable to load ${failures.join(" and ")}.` : "");
  }, []);

  const load = useCallback(async (sync = false) => {
    setLoading(true);
    setError("");
    try {
      setPayload(sync ? await api.syncDataQuality() : await api.dataQuality());
      try {
        setJobs(await api.dataJobs());
        setJobsError("");
      } catch (err) {
        setJobs(null);
        setJobsError(err instanceof Error ? err.message : "Jobs and freshness status unavailable.");
      }
      await loadInstitutionalControls();
    } catch (err) {
      setPayload(null);
      setError(err instanceof Error ? err.message : "Data quality dashboard unavailable.");
    } finally {
      setLoading(false);
    }
  }, [loadInstitutionalControls]);

  useEffect(() => {
    void load(false);
  }, [load]);

  if (loading && !payload) return <div className="loading">Loading institutional data quality checks...</div>;

  return (
    <div className="view-stack data-quality-view">
      <header className="view-head">
        <div>
          <div className="section-label">Institutional Data Quality</div>
          <h1>Research readiness dashboard</h1>
          <p>Dedicated review of stale symbols, missing data, short histories, source conflicts, refresh failures, and model coverage gaps.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(true); }}>
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

          {jobs && (
            <Panel title="Jobs & Freshness" meta={`auto-live ${jobs.auto_live.running ? "running" : "idle"} · last ${jobs.auto_live.last_run || "never"}`}>
              <div className="metric-grid compact-metrics">
                <StatTile label="Stale symbols (>5d)" value={fmtNumber(jobs.stale_count, 0)} tone={jobs.stale_count ? "warning" : "positive"} />
                <StatTile label="Recent failures" value={fmtNumber(jobs.recent_failures.length, 0)} tone={jobs.recent_failures.length ? "negative" : "positive"} />
                <StatTile label="Audit chain" value={jobs.audit_chain.status}
                  tone={jobs.audit_chain.status === "intact" ? "positive" : jobs.audit_chain.status === "broken" ? "negative" : "neutral"} />
                <StatTile label="Price revisions seen" value={fmtNumber(jobs.price_revisions_recent.length, 0)}
                  tone={jobs.price_revisions_recent.length ? "warning" : "positive"} />
              </div>
              {jobs.freshness.length > 0 && (
                <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable data freshness">
                  <table className="terminal-data-table quality-freshness-data-table">
                    <thead><tr><th scope="col">Symbol</th><th scope="col">Last bar</th><th scope="col">Age (cal. days)</th><th scope="col">Rows</th><th scope="col">Provider</th></tr></thead>
                    <tbody>{jobs.freshness.slice(0, 10).map((row) => (
                    <tr key={row.symbol}>
                      <th scope="row">{row.symbol}</th>
                      <td>{row.last_bar}</td>
                      <td className={row.age_calendar_days > 5 ? "tone-warning" : ""}>{row.age_calendar_days}</td>
                      <td>{row.rows}</td>
                      <td>{row.price_provider || "—"}</td>
                    </tr>
                  ))}</tbody>
                  </table>
                </div>
              )}
              <p className="forecast-note">{jobs.basis}</p>
            </Panel>
          )}
          {!jobs && jobsError && (
            <Panel title="Jobs & Freshness" meta="panel unavailable">
              <div className="notice warning" role="alert">{jobsError}</div>
              <button type="button" onClick={() => void load(false)}>Retry jobs and freshness</button>
            </Panel>
          )}

          <InstitutionalControls
            providers={providers}
            operations={operations}
            error={controlsError}
            busy={controlsBusy}
            onRetry={() => void loadInstitutionalControls()}
            onSync={async () => {
              setControlsBusy("sync");
              try {
                await api.syncOperationalIncidents();
                await loadInstitutionalControls();
              } catch (err) {
                setControlsError(err instanceof Error ? err.message : "Incident sync failed.");
              } finally {
                setControlsBusy("");
              }
            }}
            onVerifyBackup={async () => {
              setControlsBusy("backup");
              try {
                const result = await api.verifyBackup();
                setControlsError(`Backup verification completed: ${String(result.verification.passed ? "passed" : "failed")}.`);
                await loadInstitutionalControls();
              } catch (err) {
                setControlsError(err instanceof Error ? err.message : "Backup verification failed.");
              } finally {
                setControlsBusy("");
              }
            }}
          />

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
                <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable refresh failures">
                  <table className="terminal-data-table quality-four-data-table">
                    <thead><tr><th scope="col">Symbol</th><th scope="col">Attempted</th><th scope="col">Rows</th><th scope="col">Message</th></tr></thead>
                    <tbody>{payload.refresh_failures.map((row) => (
                    <tr key={`${row.symbol}-${row.attempted_at}`}>
                      <th scope="row"><strong>{row.symbol}</strong><small>{row.source}</small></th>
                      <td>{fmtTimestamp(row.attempted_at)}</td>
                      <td>{fmtNumber(row.rows_added, 0)}</td>
                      <td>{row.message}</td>
                    </tr>
                  ))}</tbody>
                  </table>
                </div>
              )}
            </Panel>
            <Panel title="Coverage Gaps" meta={`${payload.coverage_gaps.length} models`}>
              {payload.coverage_gaps.length === 0 ? (
                <EmptyState title="No model coverage gaps" body="Every imported model has real history coverage for every holding." />
              ) : (
                <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable coverage gaps">
                  <table className="terminal-data-table quality-four-data-table">
                    <thead><tr><th scope="col">Model</th><th scope="col">Coverage</th><th scope="col">State</th><th scope="col">Missing</th></tr></thead>
                    <tbody>{payload.coverage_gaps.map((row) => (
                    <tr key={row.model_id}>
                      <th scope="row"><strong>{row.model_name}</strong><small>{row.model_id}</small></th>
                      <td>{row.real_coverage_count}/{row.n_holdings}</td>
                      <td>{row.coverage_state}</td>
                      <td>{row.missing_tickers.join(", ") || "None"}</td>
                    </tr>
                  ))}</tbody>
                  </table>
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
              <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable data quality issues">
                <table className="terminal-data-table quality-issues-data-table">
                  <thead><tr><th scope="col">Severity</th><th scope="col">Category</th><th scope="col">Target</th><th scope="col">Detail</th><th scope="col">Next step</th></tr></thead>
                  <tbody>{payload.issues.map((issue, index) => (
                  <tr key={`${issue.category}-${issue.target}-${index}`}>
                    <td className={`quality-severity severity-${issue.severity}`}>{issue.severity}</td>
                    <td>{titleCase(issue.category)}</td>
                    <th scope="row">{issue.target}</th>
                    <td>{issue.detail}</td>
                    <td>{issue.next_step}</td>
                  </tr>
                ))}</tbody>
                </table>
              </div>
            )}
          </Panel>
          <p className="report-disclaimer">{payload.disclaimer}</p>
        </>
      ) : (
        <EmptyState title="Data quality unavailable" body="The backend did not return a data quality payload.">
          <button type="button" onClick={() => void load(false)}>Retry data quality</button>
        </EmptyState>
      )}
    </div>
  );
}

function InstitutionalControls({
  providers,
  operations,
  error,
  busy,
  onRetry,
  onSync,
  onVerifyBackup,
}: {
  providers: ProvidersResponse | null;
  operations: OperationsStatusResponse | null;
  error: string;
  busy: string;
  onRetry: () => void;
  onSync: () => Promise<void>;
  onVerifyBackup: () => Promise<void>;
}) {
  const [primaryProvider, setPrimaryProvider] = useState("fmp");
  const [backupProvider, setBackupProvider] = useState("tiingo");
  const [reconciliationSymbols, setReconciliationSymbols] = useState("");
  const [reconciliationNote, setReconciliationNote] = useState("");
  const [reconciliationId, setReconciliationId] = useState("");
  const [cutoverNote, setCutoverNote] = useState("");
  const [incidentId, setIncidentId] = useState("");
  const [incidentStatus, setIncidentStatus] = useState("acknowledged");
  const [incidentOwner, setIncidentOwner] = useState(operations?.incident_owner === "Unassigned" ? "" : operations?.incident_owner || "");
  const [incidentNote, setIncidentNote] = useState("");
  const [actionBusy, setActionBusy] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const priceProviderOptions = (providers?.providers || [])
    .filter((provider) => provider.domains.includes("prices") && !provider.research_only)
    .map((provider) => ({ value: provider.key, label: provider.name }));
  const reconciliationOptions = (providers?.reconciliations || [])
    .filter((row) => row.status === "passed")
    .map((row) => ({
      value: row.reconciliation_id,
      label: `${row.created_at.slice(0, 10)} · ${row.primary_provider}/${row.backup_provider} · ${row.compared_count} compared`,
    }));
  const incidentOptions = (operations?.incidents || [])
    .filter((incident) => incident.status !== "resolved")
    .map((incident) => ({ value: incident.incident_id, label: `${incident.target} · ${titleCase(incident.status)}` }));
  const reconciliationSymbolsParsed = [...new Set(
    reconciliationSymbols.split(/[\s,]+/).map((value) => value.trim().toUpperCase()).filter(Boolean),
  )].slice(0, 20);
  const reconciliationMinimum = providers?.reconciliation_policy.minimum_symbol_count || 2;

  useEffect(() => {
    if (incidentOwner || !operations?.incident_owner || operations.incident_owner === "Unassigned") return;
    setIncidentOwner(operations.incident_owner);
  }, [incidentOwner, operations?.incident_owner]);

  const runReconciliation = async () => {
    const symbols = reconciliationSymbolsParsed;
    if (symbols.length < reconciliationMinimum || primaryProvider === backupProvider || reconciliationNote.trim().length < 8) return;
    setActionBusy("reconciliation");
    setActionNotice("");
    try {
      const result = await api.recordProviderReconciliation({
        data_domain: "prices",
        primary_provider: primaryProvider,
        backup_provider: backupProvider,
        symbols,
        period: "3mo",
        note: reconciliationNote,
      });
      setReconciliationId(result.reconciliation.reconciliation_id);
      setActionNotice(`Reconciliation ${result.reconciliation.status}: ${result.reconciliation.compared_count}/${result.reconciliation.symbol_count} symbols compared.`);
      onRetry();
    } catch (caught) {
      setActionNotice(caught instanceof Error ? caught.message : "Provider reconciliation failed.");
    } finally {
      setActionBusy("");
    }
  };

  const approveCutover = async () => {
    const selected = providers?.reconciliations.find((row) => row.reconciliation_id === reconciliationId);
    if (!selected || cutoverNote.trim().length < 8) return;
    setActionBusy("cutover");
    setActionNotice("");
    try {
      await api.approveProviderCutover({
        data_domain: selected.data_domain,
        primary_provider: selected.primary_provider,
        backup_provider: selected.backup_provider,
        reconciliation_id: selected.reconciliation_id,
        note: cutoverNote,
      });
      setActionNotice("Provider cutover approved and added to the institutional research gate.");
      onRetry();
    } catch (caught) {
      setActionNotice(caught instanceof Error ? caught.message : "Provider cutover approval failed.");
    } finally {
      setActionBusy("");
    }
  };

  const updateIncident = async () => {
    if (!incidentId || incidentNote.trim().length < 5) return;
    setActionBusy("incident");
    setActionNotice("");
    try {
      await api.updateOperationalIncident(incidentId, {
        status: incidentStatus,
        owner: incidentOwner,
        note: incidentNote,
      });
      setIncidentNote("");
      setActionNotice(`Incident ${incidentStatus}.`);
      onRetry();
    } catch (caught) {
      setActionNotice(caught instanceof Error ? caught.message : "Incident update failed.");
    } finally {
      setActionBusy("");
    }
  };

  return (
    <Panel title="Institutional Controls" meta={providers?.controls_required ? "enforced" : "local controls disabled"}>
      {error && <div className="notice warning" role="alert">{error}</div>}
      {!providers && !operations ? (
        <EmptyState title="Institutional controls unavailable" body="Provider, incident, backup, and audit-chain status could not be loaded.">
          <button type="button" onClick={onRetry}>Retry institutional controls</button>
        </EmptyState>
      ) : (
        <>
          <div className="metric-grid compact-metrics">
            <StatTile
              label="Ready providers"
              value={`${providers?.providers.filter((row) => row.institutional_ready).length || 0}/${providers?.providers.length || 0}`}
              tone={providers?.providers.some((row) => row.institutional_ready) ? "positive" : "warning"}
            />
            <StatTile label="Approved cutovers" value={fmtNumber(providers?.cutovers.length, 0)} tone={providers?.cutovers.length ? "positive" : "warning"} />
            <StatTile label="Open incidents" value={fmtNumber(operations?.summary.open, 0)} tone={operations?.summary.open ? "warning" : "positive"} />
            <StatTile label="Audit chains" value={`${operations?.audit_chain.status || "unknown"} / ${operations?.privileged_chain.status || "unknown"}`} tone={operations?.audit_chain.status === "intact" && operations?.privileged_chain.status === "intact" ? "positive" : "warning"} />
            <StatTile label="Encrypted backups" value={fmtNumber(operations?.backup.count, 0)} tone={operations?.backup.count ? "positive" : "warning"} />
            <StatTile
              label="Restore test"
              value={operations?.latest_backup_verification?.details?.isolated_restore_tested ? "Passed" : "Not run"}
              tone={operations?.latest_backup_verification?.details?.passed ? "positive" : "warning"}
            />
            <StatTile label="Incident owner" value={operations?.incident_owner || "Unassigned"} tone={operations?.incident_owner && operations.incident_owner !== "Unassigned" ? "positive" : "warning"} />
            <StatTile label="Notification adapter" value={operations?.notification_adapter.configured ? "Configured" : "External"} tone={operations?.notification_adapter.configured ? "positive" : "warning"} />
          </div>
          {providers && (
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable institutional provider controls">
              <table className="terminal-data-table provider-control-data-table">
                <thead><tr><th scope="col">Provider</th><th scope="col">Configured</th><th scope="col">Licensed</th><th scope="col">Entitled</th><th scope="col">SLA owner</th><th scope="col">Role</th></tr></thead>
                <tbody>{providers.providers.map((provider) => (
                <tr key={provider.key}>
                  <th scope="row"><strong>{provider.name}</strong><small>{provider.domains.join(", ")}{provider.research_only ? " · research-only" : ""}</small></th>
                  <td>{provider.configured ? "Yes" : "No"}</td>
                  <td>{provider.licensed ? "Yes" : "No"}</td>
                  <td>{provider.entitled ? "Yes" : "No"}</td>
                  <td>{provider.sla_owner || "Unassigned"}</td>
                  <td>{provider.roles.join(", ") || "Not approved"}</td>
                </tr>
              ))}</tbody>
              </table>
            </div>
          )}
          {providers?.controls_required && (
            <div className="institutional-workspace">
              <section className="institutional-control-form">
                <h3>Price-provider reconciliation</h3>
                <div className="institutional-form-grid">
                  <label>Primary<TerminalSelect ariaLabel="Primary price provider" value={primaryProvider} onChange={setPrimaryProvider} options={priceProviderOptions} /></label>
                  <label>Backup<TerminalSelect ariaLabel="Backup price provider" value={backupProvider} onChange={setBackupProvider} options={priceProviderOptions} /></label>
                </div>
                <label>Symbols<input value={reconciliationSymbols} onChange={(event) => setReconciliationSymbols(event.target.value)} aria-label="Reconciliation symbols" placeholder={`At least ${reconciliationMinimum} symbols`} /></label>
                <label>Evidence note<textarea rows={2} value={reconciliationNote} onChange={(event) => setReconciliationNote(event.target.value)} aria-label="Reconciliation evidence note" /></label>
                <button type="button" onClick={() => void runReconciliation()} disabled={Boolean(actionBusy) || reconciliationSymbolsParsed.length < reconciliationMinimum || primaryProvider === backupProvider || reconciliationNote.trim().length < 8}>
                  {actionBusy === "reconciliation" ? "Reconciling..." : "Run and record reconciliation"}
                </button>
              </section>
              <section className="institutional-control-form">
                <h3>Provider cutover</h3>
                <label>Passed reconciliation<TerminalSelect ariaLabel="Passed provider reconciliation" value={reconciliationId} onChange={setReconciliationId} options={reconciliationOptions} placeholder="Select passed evidence" /></label>
                <label>Approval note<textarea rows={2} value={cutoverNote} onChange={(event) => setCutoverNote(event.target.value)} aria-label="Provider cutover approval note" /></label>
                <button type="button" onClick={() => void approveCutover()} disabled={Boolean(actionBusy) || !reconciliationId || cutoverNote.trim().length < 8}>
                  {actionBusy === "cutover" ? "Approving..." : "Approve provider cutover"}
                </button>
              </section>
              <section className="institutional-control-form">
                <h3>Incident ownership</h3>
                <label>Incident<TerminalSelect ariaLabel="Operational incident" value={incidentId} onChange={setIncidentId} options={incidentOptions} placeholder="Select open incident" /></label>
                <div className="institutional-form-grid">
                  <label>Status<TerminalSelect ariaLabel="Incident status" value={incidentStatus} onChange={setIncidentStatus} options={[
                    { value: "acknowledged", label: "Acknowledged" },
                    { value: "resolved", label: "Resolved" },
                  ]} /></label>
                  <label>Owner<input value={incidentOwner} onChange={(event) => setIncidentOwner(event.target.value)} aria-label="Incident owner" /></label>
                </div>
                <label>Action note<textarea rows={2} value={incidentNote} onChange={(event) => setIncidentNote(event.target.value)} aria-label="Incident action note" /></label>
                <button type="button" onClick={() => void updateIncident()} disabled={Boolean(actionBusy) || !incidentId || incidentNote.trim().length < 5}>
                  {actionBusy === "incident" ? "Updating..." : "Update incident"}
                </button>
              </section>
            </div>
          )}
          {actionNotice && <div className="form-feedback" role="status">{actionNotice}</div>}
          <div className="row-actions institutional-actions">
            <button type="button" onClick={() => void onSync()} disabled={Boolean(busy)}>{busy === "sync" ? "Syncing..." : "Sync incidents"}</button>
            <button type="button" onClick={() => void onVerifyBackup()} disabled={Boolean(busy)}>{busy === "backup" ? "Verifying..." : "Verify latest backup"}</button>
            <button type="button" onClick={onRetry} disabled={Boolean(busy)}>Refresh controls</button>
          </div>
          {providers?.disclaimer && <p className="forecast-note">{providers.disclaimer}</p>}
        </>
      )}
    </Panel>
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

import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { ModelEditPreviewResponse, ModelGovernanceResponse, ModelGovernanceRow, ModelSummary, ModelTemplate, ModelValidationResponse, ModelValidationRow } from "../api/types";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtPct } from "../utils/format";

const GOVERNANCE_SOURCE_LABEL = "api.modelGovernance";

export function Models({
  models,
  governance,
  templates,
  onImportTemplate,
  onRecordGovernance,
  onModelEdited,
  onOpenModel,
  onOpenClinic,
}: {
  models: ModelSummary[];
  governance: ModelGovernanceResponse | null;
  templates: ModelTemplate[];
  onImportTemplate: (slug: string) => Promise<void>;
  onRecordGovernance: (
    id: string,
    payload: { actor?: string; action?: string; note?: string; approval_status?: string },
  ) => Promise<void>;
  onModelEdited: () => Promise<void>;
  onOpenModel: (id: string) => void;
  onOpenClinic: (id: string) => void;
}) {
  const [importing, setImporting] = useState("");
  const [actor, setActor] = useState("Advisor Console");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [recording, setRecording] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editorRows, setEditorRows] = useState<Array<{ ticker: string; weight_pct: string }>>([]);
  const [changeNote, setChangeNote] = useState("");
  const [editorPreview, setEditorPreview] = useState<ModelEditPreviewResponse | null>(null);
  const [editorNotice, setEditorNotice] = useState("");
  const [editorBusy, setEditorBusy] = useState("");
  const [validation, setValidation] = useState<ModelValidationResponse | null>(null);
  const [validationError, setValidationError] = useState("");
  const [validationLoading, setValidationLoading] = useState(false);
  const governanceById = useMemo(() => {
    return new Map((governance?.models || []).map((row) => [row.id, row]));
  }, [governance]);
  const governanceRows = models
    .map((model) => governanceById.get(model.id))
    .filter((row): row is ModelGovernanceRow => Boolean(row));
  const modelSignature = useMemo(() => models.map((model) => `${model.id}:${model.n_holdings}:${model.real_coverage_count || 0}`).join("|"), [models]);

  const loadValidation = useCallback(async () => {
    if (!models.length) {
      setValidation(null);
      setValidationError("");
      return;
    }
    setValidationLoading(true);
    setValidationError("");
    try {
      const payload = await api.modelValidation();
      setValidation(payload);
    } catch (error) {
      setValidation(null);
      setValidationError(error instanceof Error ? error.message : "Model validation unavailable.");
    } finally {
      setValidationLoading(false);
    }
  }, [models.length]);

  useEffect(() => {
    void loadValidation();
  }, [loadValidation, modelSignature]);

  const importTemplate = async (slug: string) => {
    setImporting(slug);
    try {
      await onImportTemplate(slug);
    } finally {
      setImporting("");
    }
  };
  const recordGovernance = async (id: string, action: string, approvalStatus = "") => {
    setRecording(`${id}:${action}`);
    try {
      await onRecordGovernance(id, {
        actor,
        action,
        approval_status: approvalStatus,
        note: notes[id] || `${action.replace(/_/g, " ")} recorded from Model Governance workspace.`,
      });
      setNotes((current) => ({ ...current, [id]: "" }));
    } finally {
      setRecording("");
    }
  };
  const startEdit = (model: ModelSummary) => {
    setEditingId(model.id);
    setEditorRows((model.holdings || []).map((holding) => ({
      ticker: holding.ticker,
      weight_pct: String(holding.weight_pct ?? Math.round((holding.weight || 0) * 10000) / 100),
    })));
    setChangeNote("");
    setEditorPreview(null);
    setEditorNotice("");
  };
  const updateEditorRow = (index: number, patch: Partial<{ ticker: string; weight_pct: string }>) => {
    setEditorRows((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row));
  };
  const addEditorRow = () => setEditorRows((rows) => [...rows, { ticker: "", weight_pct: "" }]);
  const removeEditorRow = (index: number) => setEditorRows((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
  const editorPayload = (rows = editorRows) => rows
    .map((row) => ({ ticker: row.ticker.trim().toUpperCase(), weight_pct: Number(row.weight_pct) || 0 }))
    .filter((row) => row.ticker && row.weight_pct > 0);
  const previewEdit = async (rebalanceToTarget = false, rows = editorRows) => {
    if (!editingId) return;
    setEditorBusy("preview");
    setEditorNotice("");
    try {
      const result = await api.previewModelEdit(editingId, { holdings: editorPayload(rows), rebalance_to_target: rebalanceToTarget });
      setEditorPreview(result);
      setEditorNotice(result.risk_limit_state === "breach" ? "Preview found governance breaches before saving." : "Preview is within current governance limits.");
    } catch (error) {
      setEditorNotice(error instanceof Error ? error.message : "Preview failed.");
    } finally {
      setEditorBusy("");
    }
  };
  const rebalanceToTarget = async () => {
    const parsed = editorPayload();
    const total = parsed.reduce((sum, row) => sum + row.weight_pct, 0);
    if (!total) {
      setEditorNotice("Add positive target weights before rebalancing.");
      return;
    }
    const normalized = editorRows.map((row) => {
      const weight = Number(row.weight_pct) || 0;
      return { ...row, weight_pct: weight > 0 ? ((weight / total) * 100).toFixed(2) : row.weight_pct };
    });
    setEditorRows(normalized);
    await previewEdit(true, normalized);
  };
  const saveEdit = async () => {
    if (!editingId) return;
    setEditorBusy("save");
    setEditorNotice("");
    try {
      const result = await api.saveModelEdit(editingId, {
        actor,
        holdings: editorPayload(),
        change_note: changeNote,
        rebalance_to_target: false,
      });
      setEditorPreview(result);
      setEditorNotice(`Saved model edit v${result.event.version}.`);
      await onModelEdited();
    } catch (error) {
      setEditorNotice(error instanceof Error ? error.message : "Save failed.");
    } finally {
      setEditorBusy("");
    }
  };
  const editingModel = models.find((model) => model.id === editingId) || null;
  return (
    <div className="view-stack">
      <header className="view-head">
        <div>
          <div className="section-label">Models</div>
          <h1>Client model workspace</h1>
          <p>Model-level research stays blocked until every analyzed holding has live or uploaded price history.</p>
        </div>
      </header>
      <Panel title="Model Library" meta={`${templates.length} governed templates`}>
        {templates.length === 0 ? (
          <EmptyState title="No templates available" body="The governed model library API did not return templates." />
        ) : (
          <div className="template-grid model-library-grid">
            {templates.map((template) => (
              <article className="template-card model-library-card" key={template.slug}>
                <div>
                  <strong>{template.name}</strong>
                  <span>{template.category}</span>
                </div>
                <p>{template.thesis}</p>
                <dl className="template-meta">
                  <div><dt>Mandate</dt><dd>{template.mandate}</dd></div>
                  <div><dt>Benchmark</dt><dd>{template.benchmark}</dd></div>
                  <div><dt>Rebalance</dt><dd>{template.rebalance_rules.frequency} / {template.rebalance_rules.drift_band_pct}% band</dd></div>
                  <div><dt>Risk limits</dt><dd>Single {template.risk_limits.max_single_position_pct}% max</dd></div>
                </dl>
                <div className="template-holdings">
                  {template.holdings.slice(0, 6).map((holding) => (
                    <span key={holding.ticker}>{holding.ticker} <b>{fmtPct(holding.weight * 100)}</b></span>
                  ))}
                </div>
                <small>{template.provenance.caveat}</small>
                <button type="button" onClick={() => importTemplate(template.slug)} disabled={importing !== ""}>
                  {importing === template.slug ? "Importing..." : "Import template"}
                </button>
              </article>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Imported Models" meta={`${models.length} loaded`}>
        {models.length === 0 ? (
          <EmptyState title="No models imported" body="Upload a model CSV or Excel file to unlock model diagnostics." />
        ) : (
          <div className="terminal-table models-table" tabIndex={0} aria-label="Scrollable imported models table" onKeyDown={scrollTableByKey}>
            <div className="terminal-table__head">
              <span>Name</span><span>Mandate</span><span>Coverage</span><span>Missing</span><span>Actions</span>
            </div>
            {models.map((model) => (
              <div className="table-row" key={model.id}>
                <span><strong>{model.name}</strong><small>{model.id}</small></span>
                <span>{model.mandate_label}</span>
                <span>{model.real_coverage_count || 0}/{model.n_holdings}</span>
                <span>{model.missing_tickers?.slice(0, 3).join(", ") || "None"}</span>
                <span className="row-actions">
                  <button type="button" onClick={() => startEdit(model)}>Edit</button>
                  <button type="button" onClick={() => onOpenModel(model.id)}>Analysis</button>
                  <button type="button" onClick={() => onOpenClinic(model.id)}>Clinic</button>
                </span>
              </div>
            ))}
          </div>
        )}
      </Panel>
      <ModelValidationDashboard
        validation={validation}
        loading={validationLoading}
        error={validationError}
        modelCount={models.length}
        onRefresh={loadValidation}
      />
      <Panel title="Model Editor" meta={editingModel ? editingModel.name : "native editing"}>
        {!editingModel ? (
          <EmptyState title="Select a model to edit" body="Change holdings and target weights directly from Imported Models, then preview governance breaches before saving." />
        ) : (
          <div className="model-editor-workspace">
            <div className="model-editor-head">
              <div>
                <strong>{editingModel.name}</strong>
                <span>{editingModel.mandate_label} · {editingModel.n_holdings} current holdings</span>
                <p>Change holdings and target weights, preview breaches, require change notes, and record a governance snapshot on save.</p>
              </div>
              <div className="model-editor-actions">
                <button type="button" onClick={() => previewEdit(false)} disabled={Boolean(editorBusy)}>Preview breaches</button>
                <button type="button" onClick={rebalanceToTarget} disabled={Boolean(editorBusy)}>Rebalance to target</button>
              </div>
            </div>
            {editorNotice && <div className="form-feedback" role="status">{editorNotice}</div>}
            <div className="terminal-table model-editor-table" tabIndex={0} aria-label="Model Editor holdings table">
              <div className="terminal-table__head"><span>Ticker</span><span>Target Weight</span><span>Action</span></div>
              {editorRows.map((row, index) => (
                <div className="table-row" key={`${index}-${row.ticker}`}>
                  <input aria-label={`Ticker ${index + 1}`} value={row.ticker} onChange={(event) => updateEditorRow(index, { ticker: event.target.value })} />
                  <input aria-label={`Target weight ${index + 1}`} value={row.weight_pct} inputMode="decimal" onChange={(event) => updateEditorRow(index, { weight_pct: event.target.value })} />
                  <button type="button" onClick={() => removeEditorRow(index)} disabled={editorRows.length <= 1}>Remove</button>
                </div>
              ))}
            </div>
            <button className="side-secondary-action" type="button" onClick={addEditorRow}>Add holding</button>
            {editorPreview && (
              <div className="model-editor-preview">
                <div className="metric-grid">
                  <div className="stat-tile"><span>Risk state</span><strong className={editorPreview.risk_limit_state === "breach" ? "tone-warning" : "tone-positive"}>{formatStatus(editorPreview.risk_limit_state)}</strong><small>Preview before save</small></div>
                  <div className="stat-tile"><span>Holdings</span><strong>{editorPreview.proposed_holdings.length}</strong><small>Target rows</small></div>
                  <div className="stat-tile"><span>Max single</span><strong>{fmtPct(editorPreview.risk_limits.max_single_position_pct)}</strong><small>Governance limit</small></div>
                  <div className="stat-tile"><span>Min holdings</span><strong>{editorPreview.risk_limits.min_holdings}</strong><small>Governance limit</small></div>
                </div>
                {editorPreview.risk_limit_violations.length > 0 ? (
                  <ul className="warning-list">
                    {editorPreview.risk_limit_violations.map((violation) => <li key={`${violation.field}-${violation.ticker || "model"}`}>{violation.message}</li>)}
                  </ul>
                ) : <p className="muted">No governance breaches in the current preview.</p>}
              </div>
            )}
            <label className="model-editor-note">
              Change note
              <textarea
                value={changeNote}
                onChange={(event) => setChangeNote(event.target.value)}
                rows={3}
                placeholder="Required: explain what changed and why."
              />
            </label>
            <div className="model-editor-actions">
              <button type="button" onClick={saveEdit} disabled={Boolean(editorBusy) || changeNote.trim().length < 5}>
                {editorBusy === "save" ? "Saving..." : "Save model edit"}
              </button>
              <button type="button" onClick={() => startEdit(editingModel)} disabled={Boolean(editorBusy)}>Reset editor</button>
            </div>
          </div>
        )}
      </Panel>
      <Panel title="Model Governance" meta={governance?.summary.available ? `${governance.summary.model_count} governed` : "SQLite required"}>
        {!governance ? (
          <EmptyState title="Governance loading" body="Model governance is loaded with the model workspace." />
        ) : !governance.summary.available ? (
          <EmptyState title="Model governance unavailable" body={governance.warning || "SQLite persistence is required for versions, approvals, and archived snapshots."} />
        ) : governanceRows.length === 0 ? (
          <EmptyState title="No governed models yet" body="Import a model template or upload a model to start versioned governance." />
        ) : (
          <div className="governance-workspace">
            <div className="metric-grid governance-summary" aria-label="Model governance summary">
              <div className="stat-tile"><span>Approval Status</span><strong>{governance.summary.approved_count} approved</strong><small>{governance.summary.draft_count} draft / {governance.summary.pending_count} pending</small></div>
              <div className="stat-tile"><span>Mandate / Risk Limits</span><strong className={governance.summary.breach_count ? "tone-warning" : "tone-positive"}>{governance.summary.breach_count} breaches</strong><small>Deterministic checks only</small></div>
              <div className="stat-tile"><span>Archived Snapshots</span><strong>{governance.summary.snapshot_count}</strong><small>Versioned model states</small></div>
              <div className="stat-tile"><span>Who changed what</span><strong>{governance.summary.change_count}</strong><small>{GOVERNANCE_SOURCE_LABEL}</small></div>
            </div>
            <div className="governance-actor-row">
              <label>
                Actor
                <input value={actor} onChange={(event) => setActor(event.target.value)} aria-label="Governance actor" />
              </label>
              <p>{governance.disclaimer}</p>
            </div>
            <div className="terminal-table governance-table" tabIndex={0} aria-label="Scrollable model governance table" onKeyDown={scrollTableByKey}>
              <div className="terminal-table__head">
                <span>Model</span><span>Approval Status</span><span>Mandate / Risk Limits</span><span>Rebalance History</span><span>Archived Snapshots</span><span>Who changed what</span><span>Actions</span>
              </div>
              {governanceRows.map((row) => (
                <div className="table-row governance-row" key={row.id}>
                  <span><strong>{row.name}</strong><small>v{row.version} / {row.holdings_count} holdings / top {row.top_holding || "n/a"} {fmtPct(row.top_weight_pct)}</small></span>
                  <span><b className={row.approval_status === "approved" ? "tone-positive" : "tone-warning"}>{formatStatus(row.approval_status)}</b><small>{row.approved_by || "Not approved"}</small></span>
                  <span>
                    <b className={row.risk_limit_state === "breach" ? "tone-warning" : "tone-positive"}>{formatStatus(row.risk_limit_state)}</b>
                    <small>{row.risk_limit_violations[0]?.message || `Single ${row.risk_limits.max_single_position_pct}% max / min ${row.risk_limits.min_holdings} holdings`}</small>
                  </span>
                  <span><b>{formatStatus(row.rebalance_status)}</b><small>{row.last_rebalance_at ? formatDate(row.last_rebalance_at) : `${row.rebalance_rules.frequency} / ${row.rebalance_rules.drift_band_pct}% band`}</small></span>
                  <span><b>{row.snapshot_count}</b><small>{row.provenance.version ? `Template v${row.provenance.version}` : row.provenance.source_type}</small></span>
                  <span><b>{row.updated_by || "No changes"}</b><small>{row.latest_change_note || "Add a change note before committee review."}</small></span>
                  <span className="governance-actions">
                    <input
                      value={notes[row.id] || ""}
                      onChange={(event) => setNotes((current) => ({ ...current, [row.id]: event.target.value }))}
                      placeholder="Change notes"
                      aria-label={`Change notes for ${row.name}`}
                    />
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "approval_update", "approved")}>
                      {recording === `${row.id}:approval_update` ? "Saving..." : "Approve"}
                    </button>
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "archive_snapshot")}>
                      Snapshot
                    </button>
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "rebalance_recorded")}>
                      Rebalance
                    </button>
                  </span>
                </div>
              ))}
            </div>
            <div className="governance-log-grid">
              <section>
                <h2>Rebalance History</h2>
                {governance.rebalance_history.length === 0 ? (
                  <p className="muted">No rebalance events recorded.</p>
                ) : governance.rebalance_history.slice(0, 5).map((event) => (
                  <article key={event.id}><strong>{event.model_id}</strong><span>{formatDate(event.created_at)} by {event.actor}</span><p>{event.note || "Rebalance event recorded."}</p></article>
                ))}
              </section>
              <section>
                <h2>Archived Snapshots</h2>
                {governance.snapshots.length === 0 ? (
                  <p className="muted">No archived snapshots yet.</p>
                ) : governance.snapshots.slice(0, 5).map((snapshot) => (
                  <article key={snapshot.event_id}><strong>{snapshot.model_name} v{snapshot.version}</strong><span>{formatDate(snapshot.created_at)} / {snapshot.holding_count} holdings</span><p>{formatStatus(snapshot.risk_limit_state)} / {formatStatus(snapshot.approval_status || "draft")}</p></article>
                ))}
              </section>
              <section>
                <h2>Who changed what</h2>
                {governance.change_log.length === 0 ? (
                  <p className="muted">No governance changes recorded.</p>
                ) : governance.change_log.slice(0, 5).map((event) => (
                  <article key={event.id}><strong>{event.actor}</strong><span>{event.model_id} v{event.version} / {formatStatus(event.action)}</span><p>{event.note || "Governance event recorded."}</p></article>
                ))}
              </section>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function ModelValidationDashboard({
  validation,
  loading,
  error,
  modelCount,
  onRefresh,
}: {
  validation: ModelValidationResponse | null;
  loading: boolean;
  error: string;
  modelCount: number;
  onRefresh: () => Promise<void>;
}) {
  const rows = validation?.models || [];
  const champion = validation?.champion || null;
  return (
    <Panel title="Model Validation Dashboard" meta={validation ? `${validation.summary.eligible_count} evidence-ready / ${validation.summary.alert_count} alerts` : "Champion / Challenger"}>
      <div className="model-validation-head">
        <div>
          <strong>Champion / Challenger</strong>
          <span>Walk-forward evidence, decay, false positives, regime sensitivity, and drift alerts in one governance screen.</span>
        </div>
        <button type="button" onClick={() => void onRefresh()} disabled={loading || modelCount === 0}>
          {loading ? "Refreshing..." : "Refresh validation"}
        </button>
      </div>
      {error && <div className="notice warning">{error}</div>}
      {modelCount === 0 ? (
        <EmptyState title="No models to validate" body="Import or upload a model before running champion/challenger validation." />
      ) : loading && !validation ? (
        <EmptyState title="Validation loading" body="Running deterministic walk-forward evidence across available models." />
      ) : !validation ? (
        <EmptyState title="Model validation unavailable" body="The backend did not return a model validation payload." />
      ) : (
        <div className="model-validation-workspace">
          <div className="metric-grid validation-summary" aria-label="Model validation summary">
            <div className="stat-tile"><span>Champion</span><strong>{champion?.model_name || "None"}</strong><small>{champion ? `Score ${fmtNumber(champion.validation_score, 1)} / grade ${champion.validation_grade}` : "No eligible model"}</small></div>
            <div className="stat-tile"><span>Challengers</span><strong>{validation.summary.challenger_count}</strong><small>{validation.summary.eligible_count} evidence-ready</small></div>
            <div className="stat-tile"><span>Drift Alerts</span><strong className={validation.summary.alert_count ? "tone-warning" : "tone-positive"}>{validation.summary.alert_count}</strong><small>Blocked, high, and medium</small></div>
            <div className="stat-tile"><span>Governance</span><strong>{validation.summary.governance_available ? "Available" : "Unavailable"}</strong><small>Versions and approvals</small></div>
          </div>
          <div className="terminal-table model-validation-table" tabIndex={0} aria-label="Model Validation Dashboard table" onKeyDown={scrollTableByKey}>
            <div className="terminal-table__head">
              <span>Model</span><span>Champion / Challenger</span><span>Walk-Forward Evidence</span><span>False Positives</span><span>Regime Sensitivity</span><span>Signal Decay</span><span>Drift Alerts</span>
            </div>
            {rows.map((row) => (
              <div className="table-row validation-row" key={row.model_id}>
                <span><strong>{row.model_name}</strong><small>{row.model_id} · {formatStatus(row.mandate)}</small></span>
                <span><b className={row.role === "champion" ? "tone-positive" : row.validation_state === "blocked" ? "tone-warning" : ""}>{formatStatus(row.role)}</b><small>Score {fmtNumber(row.validation_score, 1)} · Grade {row.validation_grade}</small></span>
                <span>{fmtPct(row.walk_forward.hit_rate_pct)} hit rate<small>{row.walk_forward.window_count || 0} windows · alpha {fmtPct(row.walk_forward.avg_alpha_pct)}</small></span>
                <span>{row.false_positives.count || 0} flagged<small>{fmtPct(row.false_positives.rate_pct)} FP rate</small></span>
                <span>{bestRegime(row) || "No regime bucket"}<small>{row.regime_sensitivity.length} buckets</small></span>
                <span>{decaySummary(row)}<small>{row.decay.length} horizons</small></span>
                <span>{row.drift_alerts[0]?.title || "No drift alert"}<small>{row.drift_alerts[0]?.detail || row.required_action}</small></span>
              </div>
            ))}
          </div>
          <div className="governance-log-grid validation-detail-grid">
            <section>
              <h2>Walk-Forward Evidence</h2>
              {rows.slice(0, 4).map((row) => (
                <article key={`${row.model_id}-walk`}><strong>{row.model_name}</strong><span>{fmtPct(row.walk_forward.hit_rate_pct)} hit rate / {fmtPct(row.walk_forward.avg_alpha_pct)} alpha vs benchmark</span><p>{row.evidence_unavailable ? row.required_action : `${row.walk_forward.first_signal_date || "start"} to ${row.walk_forward.last_signal_date || "latest"}`}</p></article>
              ))}
            </section>
            <section>
              <h2>Regime Sensitivity</h2>
              {rows.slice(0, 4).map((row) => (
                <article key={`${row.model_id}-regime`}><strong>{row.model_name}</strong><span>{bestRegime(row) || "No regime sensitivity"}</span><p>{row.regime_sensitivity.map((item) => `${formatStatus(item.regime)} ${fmtPct(item.hit_rate_pct)}`).join(" · ") || "Awaiting eligible walk-forward windows."}</p></article>
              ))}
            </section>
            <section>
              <h2>Drift Alerts</h2>
              {validation.alerts.length === 0 ? (
                <p className="muted">No blocked, high, or medium validation alerts.</p>
              ) : validation.alerts.slice(0, 6).map((alert, index) => (
                <article key={`${alert.model_id}-${index}`}><strong>{alert.title}</strong><span>{alert.model_name || alert.model_id} · {formatStatus(alert.severity)}</span><p>{alert.detail}</p></article>
              ))}
            </section>
          </div>
          <p className="muted">{validation.disclaimer}</p>
        </div>
      )}
    </Panel>
  );
}

function bestRegime(row: ModelValidationRow) {
  const sorted = [...row.regime_sensitivity].sort((a, b) => (b.hit_rate_pct || 0) - (a.hit_rate_pct || 0));
  const best = sorted[0];
  return best ? `${formatStatus(best.regime)} · ${fmtPct(best.hit_rate_pct)}` : "";
}

function decaySummary(row: ModelValidationRow) {
  if (!row.decay.length) return "No decay";
  const last = row.decay[row.decay.length - 1];
  return `${last.horizon_days}d · ${fmtPct(last.avg_alpha_pct)}`;
}

function formatStatus(value: string) {
  return (value || "unknown").replace(/_/g, " ").replace(/\b\w/g, (char: string) => char.toUpperCase());
}

function formatDate(value?: string | null) {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function scrollTableByKey(event: KeyboardEvent<HTMLDivElement>) {
  const table = event.currentTarget;
  const pageStep = Math.max(160, table.clientHeight - 56);
  if (event.key === "ArrowRight") {
    event.preventDefault();
    table.scrollLeft += 80;
    return;
  }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    table.scrollLeft -= 80;
    return;
  }
  if (event.key === "PageDown") {
    event.preventDefault();
    table.scrollTop += pageStep;
  }
  if (event.key === "PageUp") {
    event.preventDefault();
    table.scrollTop -= pageStep;
  }
}

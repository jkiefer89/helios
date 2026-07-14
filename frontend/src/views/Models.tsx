import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { IndependentValidationResponse, ModelEditPreviewResponse, ModelGovernanceApprovalPacket, ModelGovernanceResponse, ModelGovernanceRow, ModelSummary, ModelTemplate, ModelValidationResponse, ModelValidationRow } from "../api/types";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtNumber, fmtPct } from "../utils/format";

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
    payload: {
      actor?: string;
      action?: string;
      note?: string;
      approval_status?: string;
      committee_identity?: {
        signer_name?: string;
        signer_role?: string;
        committee?: string;
        secret?: string;
      };
    },
  ) => Promise<void>;
  onModelEdited: () => Promise<void>;
  onOpenModel: (id: string) => void;
  onOpenClinic: (id: string) => void;
}) {
  const [importing, setImporting] = useState("");
  const [fetchingMissing, setFetchingMissing] = useState("");
  const [fetchNotice, setFetchNotice] = useState("");
  const [actor, setActor] = useState("Advisor Console");
  const [committeeIdentity, setCommitteeIdentity] = useState({
    signer_name: "Advisor Console",
    signer_role: "Committee reviewer",
    committee: "Investment Committee",
    secret: "",
  });
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
  const [approvalPacket, setApprovalPacket] = useState<ModelGovernanceApprovalPacket | null>(null);
  const [packetLoading, setPacketLoading] = useState("");
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
    const isDecision = approvalStatus === "approved" || approvalStatus === "rejected";
    try {
      await onRecordGovernance(id, {
        actor,
        action,
        approval_status: approvalStatus,
        note: notes[id] || `${action.replace(/_/g, " ")} recorded from Model Governance workspace.`,
        ...(isDecision ? { committee_identity: committeeIdentity } : {}),
      });
      setNotes((current) => ({ ...current, [id]: "" }));
      if (isDecision) {
        setCommitteeIdentity((current) => ({ ...current, secret: "" }));
      }
    } finally {
      setRecording("");
    }
  };
  const loadApprovalPacket = async (id: string) => {
    setPacketLoading(id);
    try {
      const result = await api.modelApprovalPacket(id);
      setApprovalPacket(result.packet);
    } finally {
      setPacketLoading("");
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
  const FETCH_MISSING_CAP = 10;
  const fetchMissing = async (model: ModelSummary) => {
    const missing = (model.missing_tickers || []).slice(0, FETCH_MISSING_CAP);
    if (!missing.length) return;
    setFetchingMissing(model.id);
    setFetchNotice("");
    let fetched = 0;
    const failures: string[] = [];
    // Sequential on purpose: the backend live-fetch path is semaphore-bounded,
    // and one slow provider call should not fan out into ten.
    for (const ticker of missing) {
      try {
        await api.fetchLive(ticker);
        fetched += 1;
      } catch {
        failures.push(ticker);
      }
    }
    setFetchingMissing("");
    const capped = (model.missing_tickers?.length || 0) > FETCH_MISSING_CAP
      ? ` (first ${FETCH_MISSING_CAP} of ${model.missing_tickers?.length})`
      : "";
    if (failures.length) {
      setFetchNotice(
        `Fetched ${fetched}/${missing.length}${capped}; live fetch failed for ${failures.join(", ")} — upload a price CSV instead.`,
      );
      window.dispatchEvent(new Event("helios:reveal-data-intake"));
    } else {
      setFetchNotice(`Fetched live history for ${fetched} ticker${fetched === 1 ? "" : "s"}${capped}.`);
    }
    await onModelEdited();
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
        {fetchNotice && <div className="form-feedback" role="status">{fetchNotice}</div>}
        {models.length === 0 ? (
          <EmptyState title="No models imported" body="Upload a model CSV or Excel file to unlock model diagnostics." />
        ) : (
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable imported models table" onKeyDown={scrollTableByKey}>
            <table className="terminal-data-table models-data-table">
              <thead><tr><th scope="col">Name</th><th scope="col">Mandate</th><th scope="col">Coverage</th><th scope="col">Missing</th><th scope="col">Actions</th></tr></thead>
              <tbody>{models.map((model) => (
              <tr key={model.id}>
                <th scope="row"><strong>{model.name}</strong><small>{model.id}</small></th>
                <td>{model.mandate_label}</td>
                <td>{model.real_coverage_count || 0}/{model.n_holdings}</td>
                <td>{model.missing_tickers?.slice(0, 3).join(", ") || "None"}</td>
                <td className="row-actions">
                  <button type="button" onClick={() => startEdit(model)}>Edit</button>
                  <button type="button" onClick={() => onOpenModel(model.id)}>Analysis</button>
                  <button type="button" onClick={() => onOpenClinic(model.id)}>Clinic</button>
                  {(model.missing_tickers?.length || 0) > 0 && (
                    <button
                      type="button"
                      onClick={() => fetchMissing(model)}
                      disabled={fetchingMissing !== ""}
                      title="Fetch live price history for the holdings blocking research"
                    >
                      {fetchingMissing === model.id ? "Fetching..." : "Fetch missing"}
                    </button>
                  )}
                </td>
              </tr>
            ))}</tbody>
            </table>
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
      <IndependentValidationPanel models={models} />
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
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable Model Editor holdings">
              <table className="terminal-data-table model-editor-data-table">
                <thead><tr><th scope="col">Ticker</th><th scope="col">Target Weight</th><th scope="col">Action</th></tr></thead>
                <tbody>{editorRows.map((row, index) => (
                <tr key={`${index}-${row.ticker}`}>
                  <td><input aria-label={`Ticker ${index + 1}`} value={row.ticker} onChange={(event) => updateEditorRow(index, { ticker: event.target.value })} /></td>
                  <td><input aria-label={`Target weight ${index + 1}`} value={row.weight_pct} inputMode="decimal" onChange={(event) => updateEditorRow(index, { weight_pct: event.target.value })} /></td>
                  <td><button type="button" onClick={() => removeEditorRow(index)} disabled={editorRows.length <= 1}>Remove</button></td>
                </tr>
              ))}</tbody>
              </table>
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
      <Panel title="Model Governance v2" meta={governance?.summary.available ? `${governance.summary.model_count} governed` : "SQLite required"}>
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
              <div className="stat-tile"><span>Approval Packet</span><strong>{governance.summary.change_count}</strong><small>Exportable committee evidence</small></div>
            </div>
            <div className="governance-actor-row">
              <label>
                Actor
                <input value={actor} onChange={(event) => setActor(event.target.value)} aria-label="Governance actor" />
              </label>
              <div className="committee-identity-card" aria-label="Committee Identity">
                <strong>Committee Identity</strong>
                <div className="committee-identity-grid">
                  <label>
                    Signer name
                    <input
                      value={committeeIdentity.signer_name}
                      onChange={(event) => setCommitteeIdentity((current) => ({ ...current, signer_name: event.target.value }))}
                      aria-label="Committee signer name"
                    />
                  </label>
                  <label>
                    Signer role
                    <input
                      value={committeeIdentity.signer_role}
                      onChange={(event) => setCommitteeIdentity((current) => ({ ...current, signer_role: event.target.value }))}
                      aria-label="Committee signer role"
                    />
                  </label>
                  <label>
                    Committee
                    <input
                      value={committeeIdentity.committee}
                      onChange={(event) => setCommitteeIdentity((current) => ({ ...current, committee: event.target.value }))}
                      aria-label="Committee name"
                    />
                  </label>
                  <label>
                    Local approval PIN
                    <input
                      value={committeeIdentity.secret}
                      type="password"
                      autoComplete="off"
                      onChange={(event) => setCommitteeIdentity((current) => ({ ...current, secret: event.target.value }))}
                      aria-label="Local approval PIN"
                    />
                  </label>
                </div>
              </div>
              <p>{governance.disclaimer}</p>
            </div>
            <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable model governance table" onKeyDown={scrollTableByKey}>
              <table className="terminal-data-table governance-data-table">
                <thead><tr><th scope="col">Model</th><th scope="col">Approval Status</th><th scope="col">Mandate / Risk Limits</th><th scope="col">Rebalance History</th><th scope="col">Archived Snapshots</th><th scope="col">Who changed what</th><th scope="col">Actions</th></tr></thead>
                <tbody>{governanceRows.map((row) => (
                <tr className="governance-row" key={row.id}>
                  <th scope="row"><strong>{row.name}</strong><small>v{row.version} / {row.holdings_count} holdings / top {row.top_holding || "n/a"} {fmtPct(row.top_weight_pct)}</small></th>
                  <td><b className={row.approval_status === "approved" ? "tone-positive" : "tone-warning"}>{formatStatus(row.approval_status)}</b><small>{row.approved_by || "Not approved"}</small></td>
                  <td>
                    <b className={row.risk_limit_state === "breach" ? "tone-warning" : "tone-positive"}>{row.can_approve ? formatStatus(row.risk_limit_state) : "Risk-limit blocked"}</b>
                    <small>{row.approval_blocked_reason || row.risk_limit_violations[0]?.message || `Single ${row.risk_limits.max_single_position_pct}% max / min ${row.risk_limits.min_holdings} holdings`}</small>
                  </td>
                  <td><b>{formatStatus(row.rebalance_status)}</b><small>{row.last_rebalance_at ? formatDate(row.last_rebalance_at) : `${row.rebalance_rules.frequency} / ${row.rebalance_rules.drift_band_pct}% band`}</small></td>
                  <td><b>{row.snapshot_count}</b><small>{row.provenance.version ? `Template v${row.provenance.version}` : row.provenance.source_type}</small></td>
                  <td><b>{row.updated_by || "No changes"}</b><small>{row.latest_change_note || "Add a change note before committee review."}</small></td>
                  <td className="governance-actions">
                    <input
                      value={notes[row.id] || ""}
                      onChange={(event) => setNotes((current) => ({ ...current, [row.id]: event.target.value }))}
                      placeholder="Change notes"
                      aria-label={`Change notes for ${row.name}`}
                    />
                    <button type="button" disabled={Boolean(recording) || !row.can_approve} onClick={() => recordGovernance(row.id, "approval_update", "approved")}>
                      {recording === `${row.id}:approval_update` ? "Saving..." : "Approve"}
                    </button>
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "approval_update", "rejected")}>
                      Reject
                    </button>
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "archive_snapshot")}>
                      Snapshot
                    </button>
                    <button type="button" disabled={Boolean(recording)} onClick={() => recordGovernance(row.id, "rebalance_recorded")}>
                      Rebalance
                    </button>
                    <button type="button" disabled={Boolean(packetLoading)} onClick={() => void loadApprovalPacket(row.id)}>
                      {packetLoading === row.id ? "Loading..." : "Approval Packet"}
                    </button>
                  </td>
                </tr>
              ))}</tbody>
              </table>
            </div>
            {approvalPacket && (
              <section className="governance-packet-panel">
                <header>
                  <div>
                    <h2>Approval Packet</h2>
                    <p>{approvalPacket.model.name} · v{approvalPacket.version} · {formatStatus(approvalPacket.approval.status)}</p>
                  </div>
                  <div className="packet-export-links">
                    <a href={approvalPacket.export.html_url} target="_blank" rel="noreferrer">Export HTML</a>
                    <a href={approvalPacket.export.pdf_url} target="_blank" rel="noreferrer">Export PDF</a>
                  </div>
                </header>
                <div className="metric-grid">
                  <div className="stat-tile"><span>Approval Gate</span><strong className={approvalPacket.risk_gate.can_approve ? "tone-positive" : "tone-warning"}>{approvalPacket.risk_gate.can_approve ? "Pass" : "Blocked"}</strong><small>{approvalPacket.risk_gate.blocked_reason || "Risk limits pass"}</small></div>
                  <div className="stat-tile"><span>Version Diff</span><strong>{fmtPct(approvalPacket.version_diff.turnover_pct)}</strong><small>{approvalPacket.version_diff.summary}</small></div>
                  <div className="stat-tile"><span>Committee Notes</span><strong>{approvalPacket.committee_notes.length}</strong><small>Approval/rejection/change notes</small></div>
                  <div className="stat-tile"><span>Committee Identity</span><strong>{approvalPacket.committee_identity?.verified ? "Verified" : "Attested"}</strong><small>{approvalPacket.committee_identity?.signer_name || "Signer not recorded"}</small></div>
                </div>
                <div className="governance-log-grid">
                  <section>
                    <h2>Committee Identity</h2>
                    <article>
                      <strong>{approvalPacket.committee_identity?.signer_name || "Signer not recorded"}</strong>
                      <span>{approvalPacket.committee_identity?.signer_role || "Local workspace reviewer"} / {approvalPacket.committee_identity?.committee || "Local Investment Committee"}</span>
                      <p>{approvalPacket.committee_identity?.verified ? "Verified by local approval PIN." : "Local workspace attestation; configure a PIN to require verification."}</p>
                    </article>
                  </section>
                  <section>
                    <h2>Version Diff</h2>
                    {approvalPacket.version_diff.added.length === 0 && approvalPacket.version_diff.removed.length === 0 && approvalPacket.version_diff.changed_weights.length === 0 ? (
                      <p className="muted">{approvalPacket.version_diff.summary}</p>
                    ) : (
                      approvalPacket.version_diff.changed_weights.slice(0, 5).map((row) => (
                        <article key={row.ticker}><strong>{row.ticker}</strong><span>{fmtPct(row.from_weight_pct)} to {fmtPct(row.to_weight_pct)}</span><p>{fmtPct(row.change_pct)} change</p></article>
                      ))
                    )}
                  </section>
                  <section>
                    <h2>Committee Notes</h2>
                    {approvalPacket.committee_notes.length === 0 ? (
                      <p className="muted">No committee notes recorded.</p>
                    ) : approvalPacket.committee_notes.slice(0, 4).map((note) => (
                      <article key={note.event_id}><strong>{note.actor}</strong><span>{formatDate(note.created_at)} / {formatStatus(note.action)}</span><p>{note.note}</p></article>
                    ))}
                  </section>
                  <section>
                    <h2>Archived Before / After</h2>
                    <article><strong>Before snapshot</strong><span>{Array.isArray((approvalPacket.before_snapshot as { holdings?: unknown[] }).holdings) ? `${(approvalPacket.before_snapshot as { holdings?: unknown[] }).holdings?.length || 0} holdings` : "Not recorded"}</span><p>Archived before model edits when available.</p></article>
                    <article><strong>After snapshot</strong><span>{approvalPacket.after_snapshot.holdings?.length || 0} holdings</span><p>Current packet state for committee review.</p></article>
                  </section>
                </div>
              </section>
            )}
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

function IndependentValidationPanel({ models }: { models: ModelSummary[] }) {
  const [modelId, setModelId] = useState("");
  const [payload, setPayload] = useState<IndependentValidationResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [sponsor, setSponsor] = useState("");
  const [outcome, setOutcome] = useState("approved");
  const [nextReviewDue, setNextReviewDue] = useState("");
  const [note, setNote] = useState("");
  const [exception, setException] = useState({ control_key: "", owner: "", reason: "", expires_at: "", controls: "" });
  const options = models.map((model) => ({ value: model.id, label: `${model.name} · ${model.id}` }));

  const load = async (id = modelId) => {
    if (!id) return;
    setBusy("load");
    setError("");
    try {
      setPayload(await api.independentValidation(id));
    } catch (caught) {
      setPayload(null);
      setError(caught instanceof Error ? caught.message : "Independent validation unavailable.");
    } finally {
      setBusy("");
    }
  };

  const recordReview = async () => {
    if (!modelId) return;
    setBusy("review");
    setError("");
    try {
      await api.recordIndependentValidation(modelId, {
        sponsor,
        outcome,
        next_review_due: nextReviewDue || undefined,
        note,
        controls: {
          provenance: "reviewed",
          out_of_sample_evidence: "reviewed",
          implementation_controls: "reviewed",
          risk_limits: "reviewed",
        },
      });
      setNote("");
      await load(modelId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Independent review could not be recorded.");
    } finally {
      setBusy("");
    }
  };

  const createException = async () => {
    if (!modelId) return;
    setBusy("exception");
    setError("");
    try {
      await api.recordValidationException(modelId, {
        control_key: exception.control_key,
        owner: exception.owner,
        reason: exception.reason,
        expires_at: exception.expires_at,
        compensating_controls: exception.controls.split("\n").map((row) => row.trim()).filter(Boolean),
      });
      setException({ control_key: "", owner: "", reason: "", expires_at: "", controls: "" });
      await load(modelId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Validation exception could not be recorded.");
    } finally {
      setBusy("");
    }
  };

  return (
    <Panel title="Independent Model Validation" meta={payload ? formatStatus(payload.status.state) : "separate sponsor and validator"}>
      <div className="independent-validation-controls">
        <label>Model<TerminalSelect ariaLabel="Independent validation model" value={modelId} onChange={(value) => { setModelId(value); setPayload(null); }} options={options} placeholder="Select a model" /></label>
        <button type="button" onClick={() => void load()} disabled={!modelId || Boolean(busy)}>{busy === "load" ? "Loading..." : "Load validation record"}</button>
      </div>
      {error && <div className="notice warning" role="alert">{error}</div>}
      {!modelId ? (
        <EmptyState title="Select a governed model" body="Independent review is version-specific and remains separate from the model sponsor's approval workflow." />
      ) : payload ? (
        <div className="independent-validation-workspace">
          <div className={`readiness-verdict ${payload.status.passed ? "ready" : "blocked"}`}>
            <strong>{payload.status.passed ? "Independent validation current" : "Independent validation required"}</strong>
            <span>{payload.status.detail}</span>
          </div>
          <section className="dashboard-grid">
            <div className="validation-control-form">
              <h3>Record independent review</h3>
              <label>Model sponsor<input value={sponsor} onChange={(event) => setSponsor(event.target.value)} placeholder="Must differ from signed-in validator" /></label>
              <label>Outcome<TerminalSelect ariaLabel="Independent review outcome" value={outcome} onChange={setOutcome} options={[
                { value: "approved", label: "Approved" },
                { value: "conditional", label: "Conditional" },
                { value: "rejected", label: "Rejected" },
              ]} /></label>
              <label>Next review due<input type="date" value={nextReviewDue} onChange={(event) => setNextReviewDue(event.target.value)} /></label>
              <label>Review note<textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Scope, evidence reviewed, and committee conclusion" /></label>
              <button type="button" onClick={() => void recordReview()} disabled={Boolean(busy) || sponsor.trim().length < 2}>{busy === "review" ? "Recording..." : "Record independent review"}</button>
            </div>
            <div className="validation-control-form">
              <h3>Controlled exception</h3>
              <label>Control key<input value={exception.control_key} onChange={(event) => setException((current) => ({ ...current, control_key: event.target.value }))} placeholder="e.g. liquidity_data" /></label>
              <label>Owner<input value={exception.owner} onChange={(event) => setException((current) => ({ ...current, owner: event.target.value }))} /></label>
              <label>Expires<input type="date" value={exception.expires_at} onChange={(event) => setException((current) => ({ ...current, expires_at: event.target.value }))} /></label>
              <label>Reason<textarea rows={2} value={exception.reason} onChange={(event) => setException((current) => ({ ...current, reason: event.target.value }))} /></label>
              <label>Compensating controls<textarea rows={3} value={exception.controls} onChange={(event) => setException((current) => ({ ...current, controls: event.target.value }))} placeholder="One control per line" /></label>
              <button type="button" onClick={() => void createException()} disabled={Boolean(busy) || !exception.control_key || !exception.owner || !exception.reason || !exception.expires_at}>{busy === "exception" ? "Recording..." : "Open controlled exception"}</button>
            </div>
          </section>
          <section className="dashboard-grid">
            <div className="governance-log-grid">
              <section>
                <h2>Review history</h2>
                {payload.reviews.length === 0 ? <p className="muted">No independent reviews recorded.</p> : payload.reviews.slice(0, 6).map((review) => (
                  <article key={review.review_id}><strong>{formatStatus(review.outcome)}</strong><span>{review.validator} · due {review.next_review_due}</span><p>{review.note || `${review.sponsor} sponsor / version ${review.model_version}`}</p></article>
                ))}
              </section>
            </div>
            <div className="governance-log-grid">
              <section>
                <h2>Exceptions</h2>
                {payload.exceptions.length === 0 ? <p className="muted">No validation exceptions recorded.</p> : payload.exceptions.slice(0, 8).map((row) => (
                  <article key={row.exception_id}>
                    <strong>{row.control_key} · {formatStatus(row.status)}</strong>
                    <span>{row.owner} · expires {row.expires_at}</span>
                    <p>{row.reason}</p>
                    {row.status === "open" && <button type="button" onClick={async () => {
                      setBusy(`resolve:${row.exception_id}`);
                      try {
                        await api.resolveValidationException(modelId, row.exception_id, "Control owner confirmed resolution in the Helios validation workspace.");
                        await load(modelId);
                      } catch (caught) {
                        setError(caught instanceof Error ? caught.message : "Exception resolution failed.");
                      } finally {
                        setBusy("");
                      }
                    }} disabled={Boolean(busy)}>{busy === `resolve:${row.exception_id}` ? "Resolving..." : "Resolve exception"}</button>}
                  </article>
                ))}
              </section>
            </div>
          </section>
        </div>
      ) : (
        <EmptyState title="Load the validation record" body="Review cadence, version status, findings, and controlled exceptions are loaded on demand." />
      )}
    </Panel>
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
            <div className="stat-tile"><span>Champion</span><strong>{champion?.model_name || "None"}</strong><small>{champion ? `Score ${fmtNumber(champion.validation_score, 1)} / grade ${champion.validation_grade} · best of ${validation.selection?.n_trials ?? "—"} (selection-biased upward)` : "No eligible model"}</small></div>
            <div className="stat-tile"><span>Challengers</span><strong>{validation.summary.challenger_count}</strong><small>{validation.summary.eligible_count} evidence-ready</small></div>
            <div className="stat-tile"><span>Drift Alerts</span><strong className={validation.summary.alert_count ? "tone-warning" : "tone-positive"}>{validation.summary.alert_count}</strong><small>Blocked, high, and medium</small></div>
            <div className="stat-tile"><span>Governance</span><strong>{validation.summary.governance_available ? "Available" : "Unavailable"}</strong><small>Versions and approvals</small></div>
          </div>
          {champion && (
            <p className="forecast-note">
              {(() => {
                const holdout = champion.holdout_confirmation;
                const adjusted = validation.selection?.champion_adjusted;
                const holdoutText = holdout?.status === "ok"
                  ? `Holdout (untouched last ~20% of windows): ${fmtNumber(holdout.hit_rate_pct, 0)}% hit rate, ${fmtNumber(holdout.avg_alpha_pct, 2)}% avg alpha over ${holdout.measured_count} windows.`
                  : `Holdout confirmation pending — ${holdout?.measured_count ?? 0} of ${holdout?.min_required ?? 8} measured windows.`;
                const bandText = adjusted?.status === "ok"
                  ? ` Selection-adjusted hit-rate band: ${fmtNumber(adjusted.hit_rate_ci_low_pct, 0)}–${fmtNumber(adjusted.hit_rate_ci_high_pct, 0)}%.`
                  : "";
                const dsr = validation.selection?.deflated_sharpe as Record<string, number | string> | undefined;
                const pbo = validation.selection?.pbo as Record<string, number | string> | undefined;
                const dsrText = dsr?.status === "ok"
                  ? ` DSR: ${fmtNumber(Number(dsr.deflated_sharpe_probability) * 100, 0)}% probability the champion beats the luckiest of ${String(dsr.n_trials)} trials.`
                  : " DSR: accumulating (needs ≥10 windows, ≥2 trials).";
                const pboText = pbo?.status === "ok"
                  ? ` PBO: ${fmtNumber(Number(pbo.pbo) * 100, 0)}% of CSCV splits saw the in-sample winner fall to the bottom half out-of-sample.`
                  : "";
                return holdoutText + bandText + dsrText + pboText;
              })()}
            </p>
          )}
          <div className="terminal-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable Model Validation Dashboard" onKeyDown={scrollTableByKey}>
            <table className="terminal-data-table model-validation-data-table">
              <thead><tr><th scope="col">Model</th><th scope="col">Champion / Challenger</th><th scope="col">Walk-Forward Evidence</th><th scope="col">False Positives</th><th scope="col">Regime Sensitivity</th><th scope="col">Signal Decay</th><th scope="col">Drift Alerts</th></tr></thead>
              <tbody>{rows.map((row) => (
              <tr className="validation-row" key={row.model_id}>
                <th scope="row"><strong>{row.model_name}</strong><small>{row.model_id} · {formatStatus(row.mandate)}</small></th>
                <td><b className={row.role === "champion" ? "tone-positive" : row.validation_state === "blocked" ? "tone-warning" : ""}>{formatStatus(row.role)}</b><small>Score {fmtNumber(row.validation_score, 1)} · Grade {row.validation_grade}</small></td>
                <td>{fmtPct(row.walk_forward.hit_rate_pct)} hit rate<small>{row.walk_forward.window_count || 0} windows · alpha {fmtPct(row.walk_forward.avg_alpha_pct)}</small></td>
                <td>{row.false_positives.count || 0} flagged<small>{fmtPct(row.false_positives.rate_pct)} FP rate</small></td>
                <td>{bestRegime(row) || "No regime bucket"}<small>{row.regime_sensitivity.length} buckets</small></td>
                <td>{decaySummary(row)}<small>{row.decay.length} horizons</small></td>
                <td>{row.drift_alerts[0]?.title || "No drift alert"}<small>{row.drift_alerts[0]?.detail || row.required_action}</small></td>
              </tr>
            ))}</tbody>
            </table>
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

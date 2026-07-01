import { useMemo, useState, type KeyboardEvent } from "react";
import type { ModelGovernanceResponse, ModelGovernanceRow, ModelSummary, ModelTemplate } from "../api/types";
import { Panel } from "../components/cards/Panel";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtPct } from "../utils/format";

const GOVERNANCE_SOURCE_LABEL = "api.modelGovernance";

export function Models({
  models,
  governance,
  templates,
  onImportTemplate,
  onRecordGovernance,
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
  onOpenModel: (id: string) => void;
  onOpenClinic: (id: string) => void;
}) {
  const [importing, setImporting] = useState("");
  const [actor, setActor] = useState("Advisor Console");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [recording, setRecording] = useState("");
  const governanceById = useMemo(() => {
    return new Map((governance?.models || []).map((row) => [row.id, row]));
  }, [governance]);
  const governanceRows = models
    .map((model) => governanceById.get(model.id))
    .filter((row): row is ModelGovernanceRow => Boolean(row));

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
                  <button type="button" onClick={() => onOpenModel(model.id)}>Analysis</button>
                  <button type="button" onClick={() => onOpenClinic(model.id)}>Clinic</button>
                </span>
              </div>
            ))}
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

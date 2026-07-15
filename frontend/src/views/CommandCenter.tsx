import { useMemo, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { CommandCenterResponse, DataStatusResponse, MacroSummary } from "../api/types";
import { SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { MiniBars, ScoreBar } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { ResearchGate } from "../components/states/ResearchGate";
import { RequestStatus, type RequestFailureState } from "../components/states/RequestStatus";
import { isViewId, type ViewId } from "../components/layout/AppShell";
import { fmtNumber, fmtPct, fmtTimestamp } from "../utils/format";

export function CommandCenter({
  payload,
  failure,
  dataStatus,
  onOpenInstrument,
  onOpenModel,
  onOpenView,
  onRetry,
}: {
  payload: CommandCenterResponse | null;
  failure: RequestFailureState | null;
  dataStatus: DataStatusResponse | null;
  onOpenInstrument: (symbol: string) => void;
  onOpenModel: (id: string) => void;
  onOpenView: (view: ViewId) => void;
  onRetry: () => Promise<void>;
}) {
  if (!payload) {
    return (
      <div className="view-stack">
        <RequestStatus failure={failure} onRetry={() => void onRetry()} />
        <EmptyState title="Command Center unavailable" body="The backend did not return a command payload.">
          {!failure && <button type="button" onClick={() => void onRetry()}>Retry Command Center</button>}
        </EmptyState>
      </div>
    );
  }
  const regime = payload.regime;
  const regimeUnavailable = regime.status === "unavailable" || typeof regime.score !== "number";
  // Narrowed for the meter branch (only rendered when a real score exists).
  const regimeScore = typeof regime.score === "number" ? regime.score : 0;
  const topDrivers = regime.drivers.slice(0, 5);
  const sourceCounts = payload.data_provenance?.source_counts || {};
  const sourceStatus = formatSourceStatus(sourceCounts);
  const generatedLabel = formatTimestamp(payload.generated_at);
  const statusItems = buildStatusItems(payload, sourceStatus, generatedLabel);
  return (
    <div className="view-stack command-terminal">
      <RequestStatus failure={failure} stale={Boolean(failure)} onRetry={() => void onRetry()} />
      {regimeUnavailable ? (
        <Panel title="Market Regime" meta="benchmark data required">
          <EmptyState
            title="Market regime unavailable"
            body={regime.summary || "No benchmark price history loaded — regime unavailable."}
          >
            <button type="button" onClick={() => onOpenView("setup")}>
              Fetch or upload benchmark data
            </button>
          </EmptyState>
        </Panel>
      ) : (
      <section className="regime-terminal">
        <header className="regime-panel-head">
          <span>Market Regime Model <InfoMark /></span>
          <span><b>Research Gate</b> {payload.eligible_for_real_research ? "Open" : "Closed"} <i>{generatedLabel}</i></span>
        </header>
        <div className="regime-copy">
          <h1>{regime.label.toUpperCase()}</h1>
          <strong>{payload.display_label}</strong>
          <p>{regime.summary}</p>
        </div>
        <div className="regime-meter" aria-label={`Regime score ${fmtNumber(regimeScore, 0)} out of 100`}>
          <button className="regime-segment risk-off" type="button" onClick={() => onOpenView("strategy")}>
            <span>Risk-off</span>
            <b>{fmtNumber(Math.max(0, 100 - regimeScore), 0)}<small>/100</small></b>
            <em>Defensive pressure</em>
          </button>
          <button className="regime-segment risk-on active" type="button" onClick={() => onOpenView("analysis")}>
            <span>{regime.label}</span>
            <b>{fmtNumber(regimeScore, 0)}<small>/100</small></b>
            <em>{payload.eligible_for_real_research ? "Real-data review enabled" : "Regime evidence unavailable"}</em>
          </button>
          <button className="regime-segment neutral" type="button" onClick={() => onOpenView("opportunities")}>
            <span>Neutral</span>
            <b>{fmtNumber(Math.max(0, 100 - Math.abs(regimeScore - 50) * 2), 0)}<small>/100</small></b>
            <em>Balance signal</em>
          </button>
        </div>
        <div className="regime-scale" role="img" aria-label={`Regime score ${fmtNumber(regimeScore, 0)} on a 0 to 100 scale`}>
          <div className="regime-scale__track">
            <i className="regime-scale__needle" style={{ left: `${Math.max(0, Math.min(100, regimeScore))}%` }} />
          </div>
          <div className="regime-scale__legend">
            <span>0 · Risk-off</span>
            <b>Score {fmtNumber(regimeScore, 0)}</b>
            <span>100 · Risk-on</span>
          </div>
        </div>
        <div className="regime-drivers">
          <div className="panel-kicker">Key Drivers</div>
          {topDrivers.map((driver) => (
            <span key={`${driver.name}-${driver.value}`}>
              <b>{driver.name}</b>
              <em className={driver.impact >= 0 ? "up" : "down"}>{driver.impact >= 0 ? "+" : ""}{fmtNumber(driver.impact, 1)}</em>
            </span>
          ))}
        </div>
      </section>
      )}

      {regime.warnings.length > 0 && <div className="warning-list command-warnings">{regime.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
      <WorkflowReadiness payload={payload} dataStatus={dataStatus} onOpenView={onOpenView} />

      {payload.macro && <MacroIntelligencePanel macro={payload.macro} />}

      <section className="command-card-grid">
        <Panel title="Top Opportunities" meta={<PanelAction label="View All" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_opportunities.length === 0 ? (
            <LockedStatePanel
              title="Opportunity rankings locked"
              body={payload.required_action || "Live ticker data or uploaded real price history must pass provenance before rankings appear."}
              actions={[
                { label: "Fetch live data", view: "setup" },
                { label: "Upload price CSV", view: "setup" },
              ]}
              onOpenView={onOpenView}
            />
          ) : payload.top_opportunities.map((item, index) => (
            <button className="rank-row" type="button" key={item.id} onClick={() => onOpenInstrument(item.symbol)}>
              <b>{index + 1}</b>
              <span><strong>{item.symbol} · {item.name}</strong><small>{item.reason}</small></span>
              <em>{fmtNumber(item.score, 1)}</em>
              <SourcePill source={item.source} />
            </button>
          ))}
        </Panel>
        <Panel title="Top Risks" meta={<PanelAction label="View All" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_risks.length === 0 ? (
            <LockedStatePanel
              title="Risk ranking locked"
              body="Drawdown, volatility, and risk scores are hidden until eligible live or uploaded history exists."
              actions={[
                { label: "Open real data setup", view: "setup" },
                { label: "Run Strategy Lab later", view: "strategy" },
              ]}
              onOpenView={onOpenView}
            />
          ) : payload.top_risks.map((item) => (
            <button className="risk-row" type="button" key={item.id} onClick={() => onOpenInstrument(item.symbol)}>
              <span><strong>{item.symbol}</strong><small>Drawdown {fmtPct(item.max_drawdown_pct)} · vol {fmtPct(item.expected_vol_pct)}</small></span>
              <em>{fmtNumber(item.risk_score, 0)}</em>
            </button>
          ))}
        </Panel>
        <Panel title="Research Queue" meta={<PanelAction label="Evidence Work" onClick={() => onOpenView("reports")} />}>
          <ResearchQueueRows payload={payload} onOpenView={onOpenView} />
        </Panel>
        <Panel title="Model Alerts" meta={<PanelAction label="View All" onClick={() => onOpenView("clinic")} />}>
          {payload.model_alerts.length === 0 ? (
            <LockedStatePanel
              title="Model alerts locked"
              body="Alerts are generated only after an imported model and every analyzed holding pass real-data provenance checks."
              actions={[
                { label: "Upload model", view: "models" },
                { label: "Resolve holdings", view: "setup" },
              ]}
              onOpenView={onOpenView}
            />
          ) : payload.model_alerts.map((alert) => (
            <button className={`alert-row severity-${safeSeverity(alert.severity)}`} type="button" key={alert.id} onClick={() => onOpenModel(alert.id)}>
              <strong>{alert.name}</strong><span>{alert.message}</span><small>{alert.next_step}</small>
            </button>
          ))}
        </Panel>
      </section>

      <section className="command-lower-grid">
        <Panel title="Opportunity Radar" meta={<PanelAction label="Opportunity Score" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_opportunities.length === 0 ? (
            <LockedRadarTable message={payload.required_action || "Upload or fetch real price history to unlock the radar table."} onOpenView={onOpenView} />
          ) : (
            <div className="radar-preview-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable opportunity radar preview" onKeyDown={scrollTableByKey}>
              <table className="terminal-data-table command-radar-table">
                <thead><tr><th scope="col">Rank</th><th scope="col">Instrument</th><th scope="col">Score</th><th scope="col">Risk</th><th scope="col">Drivers</th></tr></thead>
                <tbody>{payload.top_opportunities.map((item, index) => (
                  <tr key={item.id}>
                    <td>{index + 1}</td>
                    <th scope="row"><button className="table-link" type="button" onClick={() => onOpenInstrument(item.symbol)}>{item.symbol}<small>{item.name}</small></button></th>
                    <td><ScoreBar value={item.score} tone="positive" /></td>
                    <td><ScoreBar value={item.risk_score} tone="warning" /></td>
                    <td>{item.reason || item.action}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Score Distribution" meta={<PanelAction label="Real Eligible Set" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_opportunities.length || payload.top_risks.length ? (
            <MiniBars rows={[
              ...payload.top_opportunities.slice(0, 5).map((item) => ({ label: item.symbol, value: item.score, tone: "positive" })),
              ...payload.top_risks.slice(0, 5).map((item) => ({ label: `${item.symbol} risk`, value: item.risk_score, tone: "negative" })),
            ]} />
          ) : <LockedDistribution />}
        </Panel>
      </section>

      <footer className="command-status">
        <div className="command-status__left">
          <span>Data status <InfoMark /></span>
          {statusItems.map((item) => (
            <button className={`status-dot status-${item.tone}`} key={item.label} type="button" onClick={() => onOpenView(item.view)}>
              <i />
              {item.label}
            </button>
          ))}
        </div>
      </footer>
    </div>
  );
}

function PanelAction({ label, onClick }: { label: string; onClick: () => void }) {
  return <button className="panel-link" type="button" onClick={onClick}>{label}</button>;
}

function formatSourceStatus(sourceCounts: Record<string, number>) {
  const labels: Record<string, string> = {
    live: "Live histories",
    upload: "Uploaded histories",
    sample: "Ineligible fixture histories",
    simulated: "Ineligible generated histories",
    missing: "Missing histories",
  };
  const parts = Object.entries(sourceCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([source, count]) => `${labels[source] || `${source} histories`}: ${count}`);
  return parts.join(" · ") || "Source status pending";
}

function WorkflowReadiness({
  payload,
  dataStatus,
  onOpenView,
}: {
  payload: CommandCenterResponse;
  dataStatus: DataStatusResponse | null;
  onOpenView: (view: ViewId) => void;
}) {
  const missing = dataStatus?.missing_data.missing_tickers || [];
  const nextView = isViewId(payload.next_action.view) ? payload.next_action.view : "command";
  return (
    <section className="command-workflow" aria-label="Research readiness and next action">
      <ResearchGate
        payload={payload}
        state={payload.readiness.state}
        title={payload.readiness.summary}
        actionLabel={payload.next_action.label}
        onAction={() => {
          onOpenView(nextView === "instruments" ? "setup" : nextView);
        }}
      />
      <div className="command-workflow__grid">
        <Panel title="Readiness" meta={`${payload.readiness.checks.filter((check) => check.passed).length}/${payload.readiness.checks.length} controls`}>
          <div className="readiness-check-list">
            {payload.readiness.checks.map((check) => (
              <button type="button" key={check.key} onClick={() => openView(isViewId(check.view) ? check.view : "command", onOpenView)}>
                <i className={check.passed ? "passed" : "blocked"} aria-hidden="true" />
                <span><strong>{check.label}</strong><small>{check.detail}</small></span>
                <em>{check.passed ? "Pass" : "Review"}</em>
              </button>
            ))}
          </div>
        </Panel>
        <Panel title="Active Blockers" meta={`${payload.blockers.length} open`}>
          {payload.blockers.length === 0 ? (
            <EmptyState title="No active blockers" body="Required research controls currently pass. Continue to candidate evidence review." />
          ) : (
            <div className="workflow-event-list">
              {payload.blockers.slice(0, 5).map((blocker) => (
                <button type="button" key={blocker.id} onClick={() => openView(isViewId(blocker.view) ? blocker.view : "command", onOpenView)}>
                  <b className={`severity-${safeSeverity(blocker.severity)}`}>{blocker.severity}</b>
                  <span><strong>{blocker.title}</strong><small>{blocker.detail}</small><small>{blocker.required_action}</small></span>
                </button>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Recent Changes" meta={`${payload.recent_changes.length} recorded`}>
          {payload.recent_changes.length === 0 ? (
            <EmptyState title="No recent workspace changes" body="Refreshes, governance actions, and saved report snapshots appear here." />
          ) : (
            <div className="workflow-event-list">
              {payload.recent_changes.slice(0, 5).map((change) => (
                <button type="button" key={change.id} onClick={() => onOpenView(isViewId(change.view) ? change.view : "command")}>
                  <b>{change.kind.replace(/_/g, " ")}</b>
                  <span><strong>{change.title}</strong><small>{change.detail}</small><small>{fmtTimestamp(change.created_at)}</small></span>
                </button>
              ))}
            </div>
          )}
        </Panel>
      </div>
      {!payload.readiness.ready && missing.length > 0 && (
        <p className="command-workflow__missing">Missing model coverage: {missing.slice(0, 8).join(", ")}{missing.length > 8 ? ` +${missing.length - 8}` : ""}</p>
      )}
    </section>
  );
}

function ResearchQueueRows({
  payload,
  onOpenView,
}: {
  payload: CommandCenterResponse;
  onOpenView: (view: ViewId) => void;
}) {
  const rows = buildResearchQueue(payload);
  if (!payload.eligible_for_real_research || rows.length === 0) {
    return (
      <LockedStatePanel
        title="Research queue locked"
        body={payload.required_action || "Real research work appears after eligible live or uploaded evidence passes provenance checks."}
        actions={[
          { label: "Open real data setup", view: "setup" },
          { label: "Review report caveats", view: "reports" },
        ]}
        onOpenView={onOpenView}
      />
    );
  }
  return (
    <div className="research-terminal-list">
      {rows.map((item) => (
        <button className={`research-terminal-row priority-${safePriority(item.priority)}`} type="button" key={`${item.priority}-${item.title}`} onClick={() => onOpenView(item.view)}>
          <SignalIcon tone="info" />
          <span><strong>{item.title}</strong><small>{item.detail}</small></span>
          <em>{item.priority}</em>
        </button>
      ))}
    </div>
  );
}

function LockedStatePanel({
  title,
  body,
  actions,
  onOpenView,
}: {
  title: string;
  body: string;
  actions: Array<{ label: string; view: ViewId }>;
  onOpenView: (view: ViewId) => void;
}) {
  return (
    <div className="locked-state-panel">
      <strong>{title}</strong>
      <p>{body}</p>
      <div>
        {actions.map((action) => (
          <button key={action.label} type="button" onClick={() => openView(action.view, onOpenView)}>{action.label}</button>
        ))}
      </div>
    </div>
  );
}

function openView(view: ViewId, onOpenView: (view: ViewId) => void) {
  onOpenView(view);
}

function LockedRadarTable({
  message,
  onOpenView,
}: {
  message: string;
  onOpenView: (view: ViewId) => void;
}) {
  const [activeFilter, setActiveFilter] = useState<RadarFilterKey>("universe");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterConfig = useMemo(() => buildRadarFilterConfig(message), [message]);
  const activeConfig = filterConfig[activeFilter];
  const filters: Array<[string, RadarFilterKey]> = [
    ["Universe gate", "universe"],
    ["Sector gate", "sector"],
    ["Strategy gate", "strategy"],
    ["Score gate", "score"],
    ["Why locked", "advanced"],
  ];
  const rows: Array<{ area: string; status: string; evidence: string; nextStep: string; view: ViewId }> = [
    { area: "Opportunity rankings", status: "Locked", evidence: "Eligible live/uploaded price history", nextStep: "Fetch or upload a real series", view: "setup" },
    { area: "Risk scoring", status: "Locked", evidence: "Drawdown and volatility from eligible history", nextStep: "Provide eligible market history", view: "strategy" },
    { area: "Client model coverage", status: "Pending", evidence: "Holdings with real price coverage", nextStep: "Upload a model and resolve missing holdings", view: "models" },
    { area: "Report publishing", status: "Blocked", evidence: "Passed provenance gate", nextStep: "Review evidence pack after real data passes", view: "reports" },
    { area: "Portfolio Clinic", status: "Blocked", evidence: "Imported model plus holding histories", nextStep: "Run clinic after model coverage is complete", view: "clinic" },
  ];
  const updateFilter = (key: RadarFilterKey) => {
    setActiveFilter(key);
    setFiltersOpen(key !== activeFilter || !filtersOpen);
  };
  return (
    <div className="radar-terminal">
      <div className="radar-filter-bar">
        {filters.map(([label, key]) => (
          <button
            type="button"
            key={label}
            aria-pressed={activeFilter === key && filtersOpen}
            onClick={() => updateFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {filtersOpen && (
        <div className="radar-filter-panel">
          <div>
            <strong>{activeConfig.title}</strong>
            <p>{activeConfig.detail}</p>
          </div>
          <dl>
            {activeConfig.fields.map((field) => (
              <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>
            ))}
          </dl>
          <div className="radar-filter-panel__actions">
            <button type="button" onClick={() => onOpenView(activeConfig.primaryView)}>{activeConfig.primaryAction}</button>
            <button type="button" onClick={() => onOpenView("opportunities")}>Open full radar</button>
          </div>
        </div>
      )}
      <div className="radar-table-meta" aria-label="Locked opportunity radar row summary">
        <span>Evidence gates</span>
        <span>{rows.length} checks</span>
      </div>
      <div className="radar-preview-table locked-radar-table terminal-data-table-shell" tabIndex={0} aria-label="Scrollable locked opportunity radar gate checklist" onKeyDown={scrollTableByKey}>
        <table className="terminal-data-table gate-checklist-data-table">
          <thead><tr><th scope="col">Area</th><th scope="col">Status</th><th scope="col">Required evidence</th><th scope="col">Next step</th></tr></thead>
          <tbody>{rows.map((row) => (
            <tr key={row.area}>
              <th scope="row">
                <button className="table-link" type="button" onClick={() => onOpenView(row.view)}>
                  <strong>{row.area}</strong><small>{row.area === "Opportunity rankings" ? message : "Awaiting eligible real-data evidence."}</small>
                </button>
              </th>
              <td><span className="gate-status">{row.status}</span></td>
              <td>{row.evidence}</td>
              <td>{row.nextStep}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

type RadarFilterKey = "universe" | "sector" | "strategy" | "score" | "advanced";

interface RadarFilterConfig {
  title: string;
  detail: string;
  primaryAction: string;
  primaryView: ViewId;
  fields: Array<{ label: string; value: string }>;
}

function buildRadarFilterConfig(message: string): Record<RadarFilterKey, RadarFilterConfig> {
  return {
    universe: {
      title: "Universe filter",
      detail: "The Command Center radar is scoped to instruments and models that pass the real-data provenance gate.",
      primaryAction: "Open real data setup",
      primaryView: "setup",
      fields: [
        { label: "Eligible rows", value: "0" },
        { label: "Current gate", value: message },
        { label: "Included sources", value: "Live/uploaded histories only" },
      ],
    },
    sector: {
      title: "Sector filter",
      detail: "Sector filters become active after eligible live or uploaded candidates include sector metadata.",
      primaryAction: "Open candidate radar",
      primaryView: "opportunities",
      fields: [
        { label: "Selected sector", value: "All sectors" },
        { label: "Rows available", value: "No real eligible rows yet" },
        { label: "Ineligible rows", value: "Excluded from research rankings" },
      ],
    },
    strategy: {
      title: "Strategy filter",
      detail: "Strategy filters connect the radar to the deterministic Strategy Lab once a real series or model is selected.",
      primaryAction: "Open Strategy Lab",
      primaryView: "strategy",
      fields: [
        { label: "Selected strategy", value: "All strategies" },
        { label: "Backtest gate", value: "Waiting for eligible history" },
        { label: "Methodology", value: "Backend evidence model only" },
      ],
    },
    score: {
      title: "Score filter",
      detail: "Opportunity-score sorting is active only for candidates generated from eligible real data.",
      primaryAction: "Open full radar",
      primaryView: "opportunities",
      fields: [
        { label: "Sort", value: "Opportunity score" },
        { label: "Minimum score", value: "0" },
        { label: "Displayed rows", value: "Locked until provenance passes" },
      ],
    },
    advanced: {
      title: "Radar filters",
      detail: "These filters show exactly why the Command Center radar is locked and where to provide the missing evidence.",
      primaryAction: "Open real data setup",
      primaryView: "setup",
      fields: [
        { label: "Universe", value: "Real-data eligible only" },
        { label: "Score distribution", value: "Blocked without eligible rows" },
        { label: "Next action", value: "Fetch or upload real price history" },
      ],
    },
  };
}

function scrollTableByKey(event: KeyboardEvent<HTMLDivElement>) {
  const table = event.currentTarget;
  const pageStep = Math.max(80, table.clientHeight - 40);
  const keySteps: Record<string, number> = {
    ArrowDown: 36,
    ArrowUp: -36,
    PageDown: pageStep,
    PageUp: -pageStep,
  };
  if (event.key === "Home") {
    event.preventDefault();
    table.scrollTop = 0;
    return;
  }
  if (event.key === "End") {
    event.preventDefault();
    table.scrollTop = table.scrollHeight;
    return;
  }
  const step = keySteps[event.key];
  if (step === undefined) return;
  event.preventDefault();
  table.scrollTop += step;
}

function LockedDistribution() {
  return (
    <div className="distribution-empty distribution-gate" aria-label="Score distribution locked until eligible real-data rows exist">
      <div className="distribution-empty__meta">
        <span>Universe: No eligible real-data rows</span>
        <span>As of current backend payload</span>
      </div>
      <div className="distribution-gate__body">
        <strong>Score distribution locked</strong>
        <p>No histogram is rendered until real or uploaded candidates pass provenance checks.</p>
        <ul>
          <li>Fetch live data or upload eligible price history.</li>
          <li>Provide eligible live or uploaded rows before ranking.</li>
          <li>Rebuild reports only after the evidence gate opens.</li>
        </ul>
      </div>
    </div>
  );
}

function SignalIcon({ tone }: { tone: "up" | "warn" | "info" }) {
  const glyph = tone === "up" ? "↑" : tone === "warn" ? "!" : "i";
  return <span className={`signal-icon signal-${tone}`} aria-hidden="true">{glyph}</span>;
}

function InfoMark() {
  return <span className="info-mark" aria-hidden="true" />;
}

function safeSeverity(value?: string) {
  if (value === "high" || value === "medium" || value === "low") return value;
  return "medium";
}

function safePriority(value?: string) {
  if (value === "high" || value === "medium" || value === "low") return value;
  return "medium";
}

function buildResearchQueue(payload: CommandCenterResponse) {
  const defaultViews: ViewId[] = ["opportunities", "clinic", "setup", "reports"];
  return payload.research_queue.map((row, index) => ({
    ...row,
    view: defaultViews[index] || "reports",
  })).slice(0, 4);
}

interface StatusItem {
  tone: "positive" | "warning";
  label: string;
  view: ViewId;
}

function buildStatusItems(payload: CommandCenterResponse, sourceStatus: string, generatedLabel: string): StatusItem[] {
  const modeLabel = payload.display_label || "Data status pending";
  const sourceTone = payload.eligible_for_real_research ? "positive" : "warning";
  const sourceLabel = payload.eligible_for_real_research ? `Sources: ${sourceStatus}` : `Blocked/Mixed sources: ${sourceStatus}`;
  return [
    { tone: payload.eligible_for_real_research ? "positive" : "warning", label: `Market Data: ${modeLabel}`, view: "setup" },
    { tone: sourceTone, label: sourceLabel, view: "reports" },
    payload.eligible_for_real_research
      ? { tone: "positive" as const, label: "Research: unlocked by provenance checks", view: "opportunities" as const }
      : { tone: "warning" as const, label: "Research: gated until provenance passes", view: "opportunities" as const },
    { tone: "warning", label: generatedLabel.replace("Data as of ", "Models: "), view: "models" },
  ];
}

function formatTimestamp(value: string) {
  const formatted = fmtTimestamp(value);
  return formatted === "—" ? "Data timestamp pending" : `Data as of ${formatted}`;
}

/** Macro Intelligence: Fed stance (hawk/dove over official press + speeches),
    geopolitical risk index (GDELT lexicon), White House policy themes, and
    FOMC proximity — the non-numeric forecast inputs, honestly labeled. */
function MacroIntelligencePanel({ macro }: { macro: MacroSummary }) {
  const [brief, setBrief] = useState<{ summary: string; stance: string; risks: string[] } | null>(null);
  const [briefState, setBriefState] = useState<"idle" | "loading" | "error">("idle");

  const fetchBrief = async () => {
    if (briefState === "loading") return;
    setBriefState("loading");
    try {
      type TitledFeed = { documents?: Array<{ title: string }>; actions?: Array<{ title: string }> } & Record<string, unknown>;
      const snap = (await api.macro()) as {
        fed?: TitledFeed; policy?: TitledFeed; geopolitics?: Record<string, unknown>;
        fomc?: unknown; rates?: unknown;
      };
      const slim = {
        fed: { ...(snap.fed || {}), documents: undefined },
        fed_latest: (snap.fed?.documents || []).slice(0, 5).map((d) => d.title),
        policy: { ...(snap.policy || {}), actions: undefined },
        policy_latest: (snap.policy?.actions || []).slice(0, 5).map((d) => d.title),
        geopolitics: { ...(snap.geopolitics || {}), headlines: undefined },
        fomc: snap.fomc,
        rates: snap.rates,
        data_mode: "real",
      };
      const res = await api.aiMacroBrief(slim as Record<string, unknown>);
      setBrief({
        summary: res.result?.summary || "",
        stance: res.result?.stance || "",
        risks: res.result?.risks || [],
      });
      setBriefState("idle");
    } catch {
      setBriefState("error");
    }
  };

  const stance = macro.fed_stance_score ?? 0;
  const stancePos = Math.max(0, Math.min(100, (stance + 1) * 50)); // -1..1 -> 0..100
  const gpr = macro.gpr_index ?? null;
  const themes = Object.entries(macro.policy_themes || {});
  return (
    <Panel
      title="Macro Intelligence — Fed · Policy · Geopolitics"
      meta={macro.fomc_start ? `Next FOMC ${macro.fomc_start}${macro.fomc_days_until != null ? ` · ${macro.fomc_days_until}d` : ""}${macro.fomc_imminent ? " ⚑ imminent" : ""}` : undefined}
    >
      <div className="macro-grid">
        <div className="macro-cell">
          <span className="macro-cell__label">Fed stance</span>
          {macro.fed_available ? (
            <>
              <div className="macro-meter" title={`score ${fmtNumber(stance, 2)}`}>
                <span className="macro-meter__dove">DOVISH</span>
                <div className="macro-meter__track"><div className="macro-meter__pin" style={{ left: `${stancePos}%` }} /></div>
                <span className="macro-meter__hawk">HAWKISH</span>
              </div>
              <small>{(macro.fed_stance_label || "").toUpperCase()} ({fmtNumber(stance, 2)}) — lexicon over official Fed press + speeches</small>
            </>
          ) : (
            <small>Fed feed unavailable — no stance assumed.</small>
          )}
        </div>
        <div className="macro-cell">
          <span className="macro-cell__label">Geopolitical risk</span>
          {macro.gpr_available && gpr != null ? (
            <>
              <strong className={`macro-gpr macro-gpr--${macro.gpr_level || "calm"}`}>{fmtNumber(gpr * 100, 0)}<small>/100</small></strong>
              <small>{(macro.gpr_level || "").toUpperCase()} — conflict lexicon over world news (3d)</small>
            </>
          ) : (
            <small>Geopolitics feed unavailable — risk unknown, not assumed calm.</small>
          )}
        </div>
        <div className="macro-cell">
          <span className="macro-cell__label">White House policy themes</span>
          {macro.policy_available && themes.length > 0 ? (
            <div className="macro-themes">
              {themes.map(([theme, count]) => (
                <span className="macro-theme" key={theme}>{theme} ×{count}</span>
              ))}
            </div>
          ) : (
            <small>{macro.policy_available ? "No tagged themes in recent actions." : "Policy feed unavailable."}</small>
          )}
        </div>
      </div>
      <div className="macro-brief">
        <button type="button" onClick={() => void fetchBrief()} disabled={briefState === "loading"}>
          {briefState === "loading" ? "Copilot reading the tape…" : brief ? "Refresh copilot read" : "Copilot read"}
        </button>
        {briefState === "error" && <small className="macro-brief__err">Copilot brief unavailable — deterministic scores above still stand.</small>}
        {brief && (
          <div className="macro-brief__body">
            <p>{brief.summary}</p>
            {brief.stance && <p><b>Stance:</b> {brief.stance}</p>}
            {brief.risks.length > 0 && (
              <ul>{brief.risks.slice(0, 3).map((r) => <li key={r}>{r}</li>)}</ul>
            )}
          </div>
        )}
      </div>
      <p className="forecast-note">
        Ratings shrink conviction ahead of FOMC decisions and elevated geopolitical risk (transparent
        damper, floor ×0.70) — macro context argues, it never fabricates a forecast.
      </p>
    </Panel>
  );
}

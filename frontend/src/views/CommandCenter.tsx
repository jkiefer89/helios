import { useMemo, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { CommandCenterResponse, DataStatusResponse, MacroSummary } from "../api/types";
import { SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { MiniBars, ScoreBar } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import type { ViewId } from "../components/layout/AppShell";
import { fmtNumber, fmtPct, fmtTimestamp } from "../utils/format";

type DisclosureTone = "neutral" | "info" | "positive" | "warning";

export function CommandCenter({
  payload,
  dataStatus,
  onOpenInstrument,
  onOpenModel,
  onOpenView,
}: {
  payload: CommandCenterResponse | null;
  dataStatus: DataStatusResponse | null;
  onOpenInstrument: (symbol: string) => void;
  onOpenModel: (id: string) => void;
  onOpenView: (view: ViewId) => void;
}) {
  if (!payload) return <EmptyState title="Command Center unavailable" body="The backend did not return a command payload." />;
  const regime = payload.regime;
  const topDrivers = regime.drivers.slice(0, 5);
  const sourceCounts = payload.data_provenance?.source_counts || {};
  const sourceStatus = formatSourceStatus(sourceCounts);
  const generatedLabel = formatTimestamp(payload.generated_at);
  const statusItems = buildStatusItems(payload, sourceStatus, generatedLabel);
  const disclosureCards = buildDisclosureCards(payload, sourceStatus);
  return (
    <div className="view-stack command-terminal">
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
        <div className="regime-meter" aria-label={`Regime score ${fmtNumber(regime.score, 0)} out of 100`}>
          <button className="regime-segment risk-off" type="button" onClick={() => onOpenView("strategy")}>
            <span>Risk-off</span>
            <b>{fmtNumber(Math.max(0, 100 - regime.score), 0)}<small>/100</small></b>
            <em>Defensive pressure</em>
          </button>
          <button className="regime-segment risk-on active" type="button" onClick={() => onOpenView("analysis")}>
            <span>{regime.label}</span>
            <b>{fmtNumber(regime.score, 0)}<small>/100</small></b>
            <em>{payload.eligible_for_real_research ? "Real-data review enabled" : "Demo-only regime proxy"}</em>
          </button>
          <button className="regime-segment neutral" type="button" onClick={() => onOpenView("opportunities")}>
            <span>Neutral</span>
            <b>{fmtNumber(Math.max(0, 100 - Math.abs(regime.score - 50) * 2), 0)}<small>/100</small></b>
            <em>Balance signal</em>
          </button>
        </div>
        <div className="regime-scale" role="img" aria-label={`Regime score ${fmtNumber(regime.score, 0)} on a 0 to 100 scale`}>
          <div className="regime-scale__track">
            <i className="regime-scale__needle" style={{ left: `${Math.max(0, Math.min(100, regime.score))}%` }} />
          </div>
          <div className="regime-scale__legend">
            <span>0 · Risk-off</span>
            <b>Score {fmtNumber(regime.score, 0)}</b>
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

      {regime.warnings.length > 0 && <div className="warning-list command-warnings">{regime.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
      {!payload.eligible_for_real_research && (
        <ResearchUnlockCTA payload={payload} dataStatus={dataStatus} onOpenView={onOpenView} />
      )}

      {payload.macro && <MacroIntelligencePanel macro={payload.macro} />}

      <section className="command-card-grid">
        <Panel title="Top Opportunities" meta={<PanelAction label="View All" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_opportunities.length === 0 ? (
            <LockedStatePanel
              title="Opportunity rankings locked"
              body={payload.required_action || "Live ticker data or uploaded real price history must pass provenance before rankings appear."}
              actions={[
                { label: "Fetch live data", view: "instruments" },
                { label: "Upload price CSV", view: "instruments" },
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
                { label: "Open real data setup", view: "instruments" },
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
                { label: "Resolve holdings", view: "instruments" },
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
            <div className="radar-preview-table" tabIndex={0} aria-label="Scrollable opportunity radar preview" onKeyDown={scrollTableByKey}>
              <div><span>Rank</span><span>Instrument</span><span>Score</span><span>Risk</span><span>Drivers</span></div>
              {payload.top_opportunities.map((item, index) => (
                <button type="button" key={item.id} onClick={() => onOpenInstrument(item.symbol)}>
                  <span>{index + 1}</span>
                  <strong>{item.symbol}<small>{item.name}</small></strong>
                  <ScoreBar value={item.score} tone="positive" />
                  <ScoreBar value={item.risk_score} tone="warning" />
                  <em>{item.reason || item.action}</em>
                </button>
              ))}
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

      <section className="command-disclosure">
        {disclosureCards.map((card) => (
          <div key={card.title}>
            <DisclosureIcon tone={card.tone} />
            <strong>{card.title}</strong>
            <p>{card.body}</p>
          </div>
        ))}
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
        <div className="command-status__right">
          <span>© 2026 Helios Analytics, Inc. All rights reserved.</span>
          <button type="button" onClick={() => onOpenView("reports")}>Report disclosures</button>
          <button type="button" onClick={() => onOpenView("reports")}>Analysis caveats</button>
        </div>
      </footer>
    </div>
  );
}

function PanelAction({ label, onClick }: { label: string; onClick: () => void }) {
  return <button className="panel-link" type="button" onClick={onClick}>{label}</button>;
}

function buildDisclosureCards(payload: CommandCenterResponse, sourceStatus: string): Array<{ title: string; body: string; tone: DisclosureTone }> {
  const realDataReady = payload.eligible_for_real_research === true;
  return [
    {
      title: "Analysis-only: not investment advice",
      body: payload.disclaimer || "Helios provides analysis only and does not provide investment advice, brokerage services, order execution, or return guarantees.",
      tone: "neutral",
    },
    realDataReady
      ? {
          title: "Real data active",
          body: `${sourceStatus}. Research panels are using eligible live or uploaded histories; verify source quality before use.`,
          tone: "positive",
        }
      : {
          title: "Demo / gated data",
          body: `${sourceStatus}. ${payload.reason || payload.required_action || "Verify source quality before use."}`,
          tone: "info",
        },
    realDataReady
      ? {
          title: "Research unlocked",
          body: "Research rankings, alerts, and score distributions are available from eligible live or uploaded histories that passed provenance checks.",
          tone: "positive",
        }
      : {
          title: "Real data required",
          body: "Research rankings, alerts, and score distributions unlock only when eligible live or uploaded histories pass provenance checks.",
          tone: "positive",
        },
    {
      title: "No guarantees",
      body: "Helios reports evidence and caveats only. Portfolio outcomes and market prices remain uncertain.",
      tone: "warning",
    },
  ];
}

function formatSourceStatus(sourceCounts: Record<string, number>) {
  const labels: Record<string, string> = {
    live: "Live histories",
    upload: "Uploaded histories",
    sample: "Demo samples",
    simulated: "Simulated histories",
    missing: "Missing histories",
  };
  const parts = Object.entries(sourceCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([source, count]) => `${labels[source] || `${source} histories`}: ${count}`);
  return parts.join(" · ") || "Source status pending";
}

function ResearchUnlockCTA({
  payload,
  dataStatus,
  onOpenView,
}: {
  payload: CommandCenterResponse;
  dataStatus: DataStatusResponse | null;
  onOpenView: (view: ViewId) => void;
}) {
  const missing = dataStatus?.missing_data.missing_tickers || [];
  return (
    <section className="research-unlock-cta" aria-label="Real research locked">
      <div>
        <span>Real Research Locked</span>
        <h2>Demo Mode cannot power real rankings.</h2>
        <p>
          {payload.display_label || "Demo data"} is available for workflow review only. Sample or synthetic histories cannot unlock
          Opportunity Radar rankings, model alerts, or advisor reports until live ticker data or uploaded real files pass provenance checks.
        </p>
        <div className="unlock-evidence-strip">
          <span><b>{dataStatus?.real_instrument_count ?? 0}</b> eligible histories</span>
          <span><b>{dataStatus?.loaded_model_count ?? 0}</b> imported models</span>
          <span><b>{missing.length}</b> missing tickers</span>
          <span><b>{dataStatus?.database.available ? "ready" : "warning"}</b> SQLite</span>
        </div>
        {missing.length > 0 && <small className="muted">Missing: {missing.slice(0, 8).join(", ")}{missing.length > 8 ? "..." : ""}</small>}
      </div>
      <div className="unlock-actions">
        <button type="button" onClick={() => onOpenView("instruments")}>Fetch live ticker data</button>
        <button type="button" onClick={() => onOpenView("instruments")}>Upload real price CSV</button>
        <button type="button" onClick={() => onOpenView("models")}>Upload model CSV/Excel</button>
        <button type="button" onClick={() => onOpenView("data-quality")}>Review Real Data Center gates</button>
      </div>
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
          { label: "Open real data setup", view: "instruments" },
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
          <button key={action.label} type="button" onClick={() => onOpenView(action.view)}>{action.label}</button>
        ))}
      </div>
    </div>
  );
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
    { area: "Opportunity rankings", status: "Locked", evidence: "Eligible live/uploaded price history", nextStep: "Fetch or upload a real series", view: "instruments" },
    { area: "Risk scoring", status: "Locked", evidence: "Drawdown and volatility from eligible history", nextStep: "Replace demo-only rows", view: "strategy" },
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
      <div className="radar-preview-table locked-radar-table gate-checklist-table" tabIndex={0} aria-label="Scrollable locked opportunity radar gate checklist" onKeyDown={scrollTableByKey}>
        <div><span>Area</span><span>Status</span><span>Required evidence</span><span>Next step</span></div>
        {rows.map((row) => (
          <button className="locked-table-row" type="button" key={row.area} onClick={() => onOpenView(row.view)}>
            <strong>{row.area}<small>{row.area === "Opportunity rankings" ? message : "Awaiting eligible real-data evidence."}</small></strong>
            <span>{row.status}</span>
            <span>{row.evidence}</span>
            <em>{row.nextStep}</em>
          </button>
        ))}
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
      primaryView: "instruments",
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
        { label: "Demo rows", value: "Excluded from research rankings" },
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
      primaryView: "instruments",
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
          <li>Replace demo/sample-only rows before ranking.</li>
          <li>Rebuild reports only after the evidence gate opens.</li>
        </ul>
      </div>
    </div>
  );
}

function DisclosureIcon({ tone }: { tone: "neutral" | "info" | "positive" | "warning" }) {
  return (
    <span className={`disclosure-icon tone-${tone}`} aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path d="M12 3 20 7v5c0 5-3.4 8-8 9-4.6-1-8-4-8-9V7l8-4Z" />
        <path d="M12 8v5M12 16h.01" />
      </svg>
    </span>
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
  const defaultViews: ViewId[] = ["opportunities", "clinic", "instruments", "reports"];
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
  const sourceLabel = payload.eligible_for_real_research ? `Sources: ${sourceStatus}` : `Demo/Mixed sources: ${sourceStatus}`;
  return [
    { tone: payload.eligible_for_real_research ? "positive" : "warning", label: `Market Data: ${modeLabel}`, view: "instruments" },
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
      const snap = (await api.macro()) as Record<string, any>;
      const slim = {
        fed: { ...(snap.fed || {}), documents: undefined },
        fed_latest: ((snap.fed?.documents as Array<{ title: string }>) || []).slice(0, 5).map((d) => d.title),
        policy: { ...(snap.policy || {}), actions: undefined },
        policy_latest: ((snap.policy?.actions as Array<{ title: string }>) || []).slice(0, 5).map((d) => d.title),
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

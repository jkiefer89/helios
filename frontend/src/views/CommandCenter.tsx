import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { CommandCenterResponse } from "../api/types";
import { SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { MiniBars, ScoreBar } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import type { ViewId } from "../components/layout/AppShell";
import { fmtNumber, fmtPct } from "../utils/format";

export function CommandCenter({
  payload,
  onOpenInstrument,
  onOpenModel,
  onOpenView,
}: {
  payload: CommandCenterResponse | null;
  onOpenInstrument: (symbol: string) => void;
  onOpenModel: (id: string) => void;
  onOpenView: (view: ViewId) => void;
}) {
  if (!payload) return <EmptyState title="Command Center unavailable" body="The backend did not return a command payload." />;
  const regime = payload.regime;
  const topDrivers = regime.drivers.slice(0, 5);
  const sourceCounts = payload.data_provenance?.source_counts || {};
  const sourceStatus = Object.entries(sourceCounts)
    .map(([source, count]) => `${source} ${count}`)
    .join(" · ") || "source status pending";
  const generatedLabel = formatTimestamp(payload.generated_at);
  const statusItems = buildStatusItems(payload, sourceStatus, generatedLabel);
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

      <section className="command-card-grid">
        <Panel title="Top Opportunities" meta={<PanelAction label="View All" onClick={() => onOpenView("opportunities")} />}>
          {payload.top_opportunities.length === 0 ? (
            <GatedOpportunityRows requiredAction={payload.required_action} onOpenView={onOpenView} />
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
            <GatedRiskRows onOpenView={onOpenView} />
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
            <GatedAlertRows onOpenView={onOpenView} />
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
        <div>
          <DisclosureIcon tone="neutral" />
          <strong>Analysis-only: not investment advice</strong>
          <p>{payload.disclaimer}</p>
        </div>
        <div>
          <DisclosureIcon tone="info" />
          <strong>Demo / gated data</strong>
          <p>{sourceStatus}. {payload.reason || payload.required_action || "Verify source quality before use."}</p>
        </div>
        <div>
          <DisclosureIcon tone="positive" />
          <strong>Real data required</strong>
          <p>Research rankings, alerts, and score distributions unlock only when eligible live or uploaded histories pass provenance checks.</p>
        </div>
        <div>
          <DisclosureIcon tone="warning" />
          <strong>No guarantees</strong>
          <p>Helios reports evidence and caveats only. Portfolio outcomes and market prices remain uncertain.</p>
        </div>
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

function GatedOpportunityRows({
  requiredAction,
  onOpenView,
}: {
  requiredAction?: string;
  onOpenView: (view: ViewId) => void;
}) {
  const rows: GatedRow[] = [
    {
      title: "Real-data candidates locked",
      detail: requiredAction || "Connect live data or upload real price/model files to generate research.",
      meta: "Universe: gated | Model: provenance",
      score: "LOCKED",
      tone: "positive",
      view: "instruments",
    },
    {
      title: "Live history required",
      detail: "Fetch an eligible live ticker series before scoring.",
      meta: "Source: live | Evidence: missing",
      score: "REQ",
      tone: "positive",
      view: "instruments",
    },
    {
      title: "Uploaded history accepted",
      detail: "CSV or spreadsheet histories can unlock analysis.",
      meta: "Source: upload | Evidence: pending",
      score: "REQ",
      tone: "positive",
      view: "instruments",
    },
    {
      title: "Model coverage gate",
      detail: "Holdings need real price coverage before ranking.",
      meta: "Source: model | Evidence: pending",
      score: "PEND",
      tone: "positive",
      view: "models",
    },
  ];
  return <GatedRows tone="positive" rows={rows} onOpenView={onOpenView} />;
}

function GatedRiskRows({ onOpenView }: { onOpenView: (view: ViewId) => void }) {
  const rows: GatedRow[] = [
    {
      title: "Risk rankings locked",
      detail: "Risk scores appear only for eligible live/uploaded histories.",
      meta: "Risk score | Drawdown | Volatility",
      score: "LOCKED",
      tone: "negative",
      view: "opportunities",
    },
    {
      title: "Drawdown unavailable",
      detail: "Max drawdown is not computed from demo-only rows.",
      meta: "Metric: max drawdown | Source: missing",
      score: "N/A",
      tone: "negative",
      view: "strategy",
    },
    {
      title: "Volatility unavailable",
      detail: "Risk model waits for eligible price history.",
      meta: "Metric: realized vol | Source: missing",
      score: "N/A",
      tone: "negative",
      view: "analysis",
    },
    {
      title: "Synthetic alerts blocked",
      detail: "No fabricated risk rows are shown for presentation density.",
      meta: "Gate: fail-closed | Research: blocked",
      score: "HONEST",
      tone: "negative",
      view: "reports",
    },
  ];
  return <GatedRows tone="negative" rows={rows} onOpenView={onOpenView} />;
}

function GatedAlertRows({ onOpenView }: { onOpenView: (view: ViewId) => void }) {
  const rows = [
    ["up", "No model alerts", "Upload a model and provide real history for every holding.", "IDLE", "models"],
    ["warn", "Portfolio diagnostics", "Exposure and drift alerts require model holdings.", "PENDING", "clinic"],
    ["warn", "Holding coverage", "Every holding needs eligible real price history.", "PENDING", "instruments"],
    ["info", "Report workflow", "Advisor report remains blocked until provenance passes.", "BLOCKED", "reports"],
  ] as const;
  return (
    <div className="alert-terminal-list">
      {rows.map(([icon, title, detail, status, view]) => (
        <button className={`alert-terminal-row alert-${icon}`} type="button" key={title} onClick={() => onOpenView(view)}>
          <SignalIcon tone={icon} />
          <span><strong>{title}</strong><small>{detail}</small></span>
          <em>{status}</em>
        </button>
      ))}
    </div>
  );
}

function ResearchQueueRows({
  payload,
  onOpenView,
}: {
  payload: CommandCenterResponse;
  onOpenView: (view: ViewId) => void;
}) {
  return (
    <div className="research-terminal-list">
      {buildResearchQueue(payload).map((item) => (
        <button className={`research-terminal-row priority-${safePriority(item.priority)}`} type="button" key={`${item.priority}-${item.title}`} onClick={() => onOpenView(item.view)}>
          <SignalIcon tone="info" />
          <span><strong>{item.title}</strong><small>{item.detail}</small></span>
          <div className="queue-progress" aria-label={`${item.progress}% complete`}>
            <i style={{ width: `${item.progress}%` }} />
          </div>
          <em>{item.progress}%</em>
        </button>
      ))}
    </div>
  );
}

interface GatedRow {
  title: string;
  detail: string;
  meta: string;
  score: string;
  tone: "positive" | "negative";
  view: ViewId;
}

function GatedRows({
  rows,
  tone,
  onOpenView,
}: {
  rows: GatedRow[];
  tone: "positive" | "negative";
  onOpenView: (view: ViewId) => void;
}) {
  return (
    <div className={`gated-terminal-list tone-${tone}`}>
      {rows.map((row, index) => (
        <button className="gated-terminal-row" type="button" key={`${row.title}-${row.score}`} onClick={() => onOpenView(row.view)}>
          <b>{index + 1}</b>
          <span>
            <strong>{row.title}</strong>
            <small>{row.detail}</small>
            <i>{row.meta}</i>
          </span>
          <em>{row.score}</em>
        </button>
      ))}
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
  const tableRef = useRef<HTMLDivElement | null>(null);
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
  const scrollRows = (direction: -1 | 1) => {
    const table = tableRef.current;
    if (!table) return;
    const step = Math.max(120, table.clientHeight - 48);
    table.scrollTo({ top: table.scrollTop + direction * step, behavior: "smooth" });
    table.focus({ preventScroll: true });
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
      <div className="radar-scroll-controls" aria-label="Locked opportunity radar row controls">
        <span>Evidence gates</span>
        <button type="button" onClick={() => scrollRows(-1)}>Scroll up</button>
        <button type="button" onClick={() => scrollRows(1)}>Scroll down</button>
      </div>
      <div ref={tableRef} className="radar-preview-table locked-radar-table gate-checklist-table" tabIndex={0} aria-label="Scrollable locked opportunity radar gate checklist" onKeyDown={scrollTableByKey}>
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
  return <span className="info-mark" aria-hidden="true">i</span>;
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
  const rows = payload.research_queue.map((row, index) => ({
    ...row,
    progress: index === 0 ? 18 : 8,
    view: defaultViews[index] || "reports",
  }));
  if (!payload.eligible_for_real_research) {
    rows.push(
      { priority: "medium", title: "Instrument evidence gate", detail: "Eligible opportunity rows will populate after real price history is available.", progress: 0, view: "instruments" },
      { priority: "low", title: "Score distribution hold", detail: "Distribution and pagination remain disabled until real scores exist.", progress: 0, view: "opportunities" },
      { priority: "low", title: "Advisor report hold", detail: "Reports use backend evidence only and remain blocked for demo-only research.", progress: 0, view: "reports" },
    );
  }
  return rows.slice(0, 4);
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
    { tone: "warning", label: "Research: gated until provenance passes", view: "opportunities" },
    { tone: "warning", label: generatedLabel.replace("Data as of ", "Models: "), view: "models" },
  ];
}

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (!Number.isFinite(date.valueOf())) return "Data timestamp pending";
  return `Data as of ${date.toLocaleString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })}`;
}

import type { CommandCenterResponse } from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { MiniBars } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { fmtNumber, fmtPct } from "../utils/format";

export function CommandCenter({
  payload,
  onOpenInstrument,
  onOpenModel,
}: {
  payload: CommandCenterResponse | null;
  onOpenInstrument: (symbol: string) => void;
  onOpenModel: (id: string) => void;
}) {
  if (!payload) return <EmptyState title="Command Center unavailable" body="The backend did not return a command payload." />;
  const regime = payload.regime;
  return (
    <div className="view-stack">
      <DataQualityBanner payload={payload} />
      <section className="command-hero">
        <Panel eyebrow="Market Regime" title={regime.label.toUpperCase()} meta={<strong className="hero-score">{fmtNumber(regime.score, 0)}/100</strong>}>
          <p className="lead">{regime.summary}</p>
          <div className="driver-grid">
            {regime.drivers.slice(0, 6).map((driver) => (
              <StatTile
                key={`${driver.name}-${driver.value}`}
                label={driver.name}
                value={<>{driver.value}<small>{driver.impact >= 0 ? "+" : ""}{fmtNumber(driver.impact, 1)}</small></>}
                tone={driver.impact >= 0 ? "positive" : "negative"}
              />
            ))}
          </div>
          {regime.warnings.length > 0 && <div className="warning-list">{regime.warnings.map((warning) => <span key={warning}>{warning}</span>)}</div>}
        </Panel>
        <Panel eyebrow="Research Thesis" title="What Helios Thinks">
          <p className="lead">{payload.eligible_for_real_research
            ? "Review the real-data candidates below. Helios is presenting evidence queues, not recommendations or orders."
            : payload.required_action || "Upload real data to unlock research evidence."}</p>
          <div className="check-strip"><span>Verify data source</span><span>Review risks</span><span>Document caveats</span></div>
          <p className="muted">{payload.disclaimer}</p>
        </Panel>
      </section>

      <section className="dashboard-grid">
        <Panel title="Top Real Opportunities" meta="ranked evidence">
          {payload.top_opportunities.length === 0 ? (
            <EmptyState
              title="Real research locked"
              body={payload.required_action || "No live/uploaded candidates are eligible yet."}
              actions={["Fetch live data", "Upload real price history", "Upload a model with real holding history"]}
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
        <Panel title="Evidence Distribution">
          <MiniBars rows={[
            ...payload.top_opportunities.slice(0, 4).map((item) => ({ label: item.symbol, value: item.score, tone: "positive" })),
            ...payload.top_risks.slice(0, 4).map((item) => ({ label: `${item.symbol} risk`, value: item.risk_score, tone: "negative" })),
          ]} />
        </Panel>
        <Panel title="Top Risks" meta="volatility/drawdown">
          {payload.top_risks.length === 0 ? (
            <EmptyState title="Risk panel locked" body="Risk rankings appear only for eligible live/uploaded histories." />
          ) : payload.top_risks.map((item) => (
            <button className="risk-row" type="button" key={item.id} onClick={() => onOpenInstrument(item.symbol)}>
              <span><strong>{item.symbol}</strong><small>Drawdown {fmtPct(item.max_drawdown_pct)} · vol {fmtPct(item.expected_vol_pct)}</small></span>
              <em>{fmtNumber(item.risk_score, 0)}</em>
            </button>
          ))}
        </Panel>
        <Panel title="Model Alerts">
          {payload.model_alerts.length === 0 ? (
            <EmptyState title="No model alerts" body="Upload a model and provide real history for every holding." />
          ) : payload.model_alerts.map((alert) => (
            <button className={`alert-row severity-${alert.severity}`} type="button" key={alert.id} onClick={() => onOpenModel(alert.id)}>
              <strong>{alert.name}</strong><span>{alert.message}</span><small>{alert.next_step}</small>
            </button>
          ))}
        </Panel>
        <Panel title="Research Queue" className="span-2">
          <div className="queue-grid">
            {payload.research_queue.map((item) => (
              <div className={`queue-card priority-${item.priority}`} key={`${item.priority}-${item.title}`}>
                <span>{item.priority}</span><strong>{item.title}</strong><p>{item.detail}</p>
              </div>
            ))}
          </div>
        </Panel>
      </section>
    </div>
  );
}

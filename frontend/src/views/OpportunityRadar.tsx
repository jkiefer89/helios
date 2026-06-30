import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { OpportunitiesResponse, OpportunityItem } from "../api/types";
import { AICopilotPanel, opportunityCopilotActions } from "../components/ai/AICopilotPanel";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { HistogramChart, ScoreBar, ScoreScatter } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtNumber, fmtPct } from "../utils/format";

export function OpportunityRadar({
  initialPayload,
  onPayload,
  onOpenInstrument,
  onOpenModel,
}: {
  initialPayload: OpportunitiesResponse | null;
  onPayload: (payload: OpportunitiesResponse) => void;
  onOpenInstrument: (symbol: string) => void;
  onOpenModel: (id: string) => void;
}) {
  const [payload, setPayload] = useState<OpportunitiesResponse | null>(initialPayload);
  const [kind, setKind] = useState("all");
  const [minScore, setMinScore] = useState(0);
  const [includeHold, setIncludeHold] = useState(true);
  const [sort, setSort] = useState<"opportunity_score" | "evidence_score" | "risk_score">("opportunity_score");
  const [selectedId, setSelectedId] = useState<string>("");
  const [error, setError] = useState("");
  const requestSeq = useRef(0);

  const load = async () => {
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      const next = await api.opportunities({ kind, includeHold, minScore, limit: 50 });
      if (requestId !== requestSeq.current) return;
      setPayload(next);
      onPayload(next);
      setSelectedId((current) => current || next.items[0]?.id || "");
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Opportunity Radar failed.");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const items = useMemo(() => [...(payload?.items || [])].sort((a, b) => {
    const direction = sort === "risk_score" ? -1 : 1;
    return direction * ((b[sort] || 0) - (a[sort] || 0));
  }), [payload, sort]);
  const scorePoints = useMemo(() => items.map((item) => ({
    label: item.symbol,
    x: item.risk_score,
    y: item.opportunity_score,
    size: Math.max(5, Math.min(11, item.evidence_score / 10)),
    tone: safeActionTone(item.action),
    meta: `${fmtNumber(item.evidence_score, 1)} evidence`,
  })), [items]);
  const opportunityScores = useMemo(() => items.map((item) => item.opportunity_score), [items]);
  const selected = items.find((item) => item.id === selectedId) || items[0];
  const lockedRows = buildLockedOpportunityRows(payload);
  const copilotPayload = useMemo<Record<string, unknown> | null>(() => {
    if (selected) {
      return {
        opportunity: selected,
        data_mode: payload?.data_mode,
        display_label: payload?.display_label,
        eligible_for_real_research: payload?.eligible_for_real_research,
        data_provenance: payload?.data_provenance,
        regime: payload?.regime,
        disclaimer: payload?.disclaimer,
      };
    }
    if (!payload) return null;
    return {
      radar_status: {
        reason: payload.reason,
        required_action: payload.required_action,
        count: payload.count,
        total_candidates: payload.total_candidates,
      },
      data_mode: payload.data_mode,
      display_label: payload.display_label,
      eligible_for_real_research: payload.eligible_for_real_research,
      data_provenance: payload.data_provenance,
      disclaimer: payload.disclaimer,
    };
  }, [payload, selected]);

  return (
    <div className="view-stack">
      {payload && <DataQualityBanner payload={payload} />}
      <header className="view-head">
        <div>
          <div className="section-label">Opportunity Radar</div>
          <h1>Ranked real-data review queue</h1>
          <p>Scores use the backend evidence model across signal, forecast, strategy, risk, data quality, and regime compatibility.</p>
        </div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label>Kind<TerminalSelect ariaLabel="Opportunity kind" value={kind} onChange={setKind} options={[
            { value: "all", label: "All" },
            { value: "instrument", label: "Instruments" },
            { value: "model", label: "Models" },
          ]} /></label>
          <label>Sort<TerminalSelect ariaLabel="Opportunity sort" value={sort} onChange={(value) => setSort(value as typeof sort)} options={[
            { value: "opportunity_score", label: "Opportunity" },
            { value: "evidence_score", label: "Evidence" },
            { value: "risk_score", label: "Risk" },
          ]} /></label>
          <label>Min<input type="number" min={0} max={100} step={5} value={minScore} onChange={(event) => setMinScore(Number(event.target.value))} /></label>
          <label className="check"><input type="checkbox" checked={includeHold} onChange={(event) => setIncludeHold(event.target.checked)} /> Hold</label>
          <button type="submit">Refresh</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {items.length > 0 && (
        <section className="dashboard-grid">
          <Panel title="Opportunity Score Map" meta="return/risk review">
            <ScoreScatter points={scorePoints} xLabel="Risk score" yLabel="Opportunity score" />
          </Panel>
          <Panel title="Opportunity Distribution" meta={`${items.length} ranked`}>
            <HistogramChart values={opportunityScores} label="Opportunity score" min={0} max={100} buckets={10} tone="positive" />
          </Panel>
        </section>
      )}
      <section className="radar-layout">
        <Panel title="Ranked Candidates" meta={`${items.length} shown${payload?.total_candidates ? ` · ${payload.total_candidates} screened` : ""}`}>
          {items.length === 0 ? (
            <>
              <div className="radar-table-meta" aria-label="Locked opportunity radar status">
                <span>Evidence gates</span>
                <span>0 eligible rankings</span>
              </div>
              <div className="radar-preview-table locked-radar-table gate-checklist-table" tabIndex={0} aria-label="Locked opportunity radar gate checklist" onKeyDown={scrollTableByKey}>
                <div><span>Area</span><span>Status</span><span>Required evidence</span><span>Next step</span></div>
                {lockedRows.map((row) => (
                  <div className="locked-table-row" key={row.area}>
                    <strong>{row.area}<small>{row.detail}</small></strong>
                    <span>{row.status}</span>
                    <span>{row.evidence}</span>
                    <em>{row.nextStep}</em>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="radar-table-meta" aria-label="Ranked candidate row summary">
                <span>{items.length} rows</span>
                <span>{payload?.total_candidates ? `${payload.total_candidates} screened` : "Real eligible set"}</span>
              </div>
              <div className="terminal-table" tabIndex={0} aria-label="Scrollable ranked candidates table" onKeyDown={scrollTableByKey}>
                <div className="terminal-table__head"><span>Rank</span><span>Name</span><span>Action</span><span>Opp</span><span>Evidence</span><span>Risk</span></div>
                {items.map((item, index) => (
                  <button
                    className={selected?.id === item.id ? "selected" : ""}
                    type="button"
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span>{index + 1}</span>
                    <span><strong>{item.symbol}</strong><small>{item.name}</small><SourcePill source={item.source} /></span>
                    <span className={`action action-${safeActionTone(item.action)}`}>{item.action}</span>
                    <ScoreBar value={item.opportunity_score} tone="positive" />
                    <ScoreBar value={item.evidence_score} tone="info" />
                    <ScoreBar value={item.risk_score} tone="negative" />
                  </button>
                ))}
              </div>
            </>
          )}
        </Panel>
        <Panel title="Selected Candidate" meta="real drivers only">
          {selected ? (
            <CandidateDetail
              item={selected}
              onOpen={() => selected.kind === "model" && selected.model_id ? onOpenModel(selected.model_id) : onOpenInstrument(selected.symbol)}
            />
          ) : (
            <LockedCandidateDetail payload={payload} />
          )}
        </Panel>
      </section>
      <AICopilotPanel
        contextLabel={selected ? `${selected.symbol} opportunity review` : "Opportunity Radar gate review"}
        payload={copilotPayload}
        dataMode={payload?.data_mode}
        actions={opportunityCopilotActions}
      />
    </div>
  );
}

function buildLockedOpportunityRows(payload: OpportunitiesResponse | null) {
  const requiredAction = payload?.required_action || payload?.reason || "Connect live data or upload real price/model files to generate real research.";
  return [
    {
      area: "Universe eligibility",
      detail: "No placeholder candidate rows are rendered.",
      status: "Locked",
      evidence: "Eligible live or uploaded price history",
      nextStep: requiredAction,
    },
    {
      area: "Opportunity scoring",
      detail: "Scores remain unavailable until provenance checks pass.",
      status: "Locked",
      evidence: "Signal, forecast, strategy, and risk evidence",
      nextStep: "Fetch live data or upload eligible histories.",
    },
    {
      area: "Model coverage",
      detail: "Model rows require covered holdings before ranking.",
      status: "Pending",
      evidence: "Every analyzed holding has real history",
      nextStep: "Upload a client model and resolve missing holdings.",
    },
    {
      area: "Research queue",
      detail: "Sample data remains demo-only.",
      status: "Blocked",
      evidence: "Passed real-data gate",
      nextStep: "Re-run the radar after the gate opens.",
    },
  ];
}

function LockedCandidateDetail({ payload }: { payload: OpportunitiesResponse | null }) {
  return (
    <div className="locked-state-panel locked-inspection-panel">
      <strong>Selection locked</strong>
      <p>{payload?.reason || payload?.required_action || "No eligible real-data candidate is available."}</p>
      <div>
        <span className="source-pill source-demo">sample data excluded</span>
        <span className="source-pill source-excluded">no fake rows</span>
      </div>
    </div>
  );
}

function CandidateDetail({ item, onOpen }: { item: OpportunityItem; onOpen: () => void }) {
  return (
    <div className="candidate-detail">
      <div className="detail-title">
        <div><strong>{item.symbol} · {item.name}</strong><span>{item.plain_english_summary}</span></div>
        <button type="button" onClick={onOpen}>Open analysis</button>
      </div>
      <div className="metric-strip">
        <span><b>{fmtNumber(item.opportunity_score, 1)}</b> Opportunity</span>
        <span><b>{fmtNumber(item.evidence_score, 1)}</b> Evidence</span>
        <span><b>{fmtNumber(item.risk_score, 1)}</b> Risk</span>
        <span><b>{fmtPct(item.expected_return_pct)}</b> Forecast</span>
      </div>
      <div className="driver-columns">
        <DriverList title="Positive Drivers" rows={item.top_positive_drivers} />
        <DriverList title="Negative Drivers" rows={item.top_negative_drivers} />
        <DriverList title="Warnings" rows={item.warnings.length ? item.warnings : ["No major warning generated."]} />
      </div>
      <p className="muted">{item.recommended_next_step}</p>
    </div>
  );
}

function DriverList({ title, rows }: { title: string; rows: string[] }) {
  return <div><h3>{title}</h3><ul>{rows.map((row) => <li key={row}>{row}</li>)}</ul></div>;
}

function scrollTableByKey(event: KeyboardEvent<HTMLDivElement>) {
  const table = event.currentTarget;
  const pageStep = Math.max(120, table.clientHeight - 48);
  const keySteps: Record<string, number> = {
    ArrowDown: 58,
    ArrowUp: -58,
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

function safeActionTone(action?: string) {
  const normalized = (action || "").toLowerCase();
  if (normalized === "buy" || normalized === "sell" || normalized === "hold" || normalized === "review") return normalized;
  return "review";
}

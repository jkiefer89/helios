import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { OpportunitiesResponse, OpportunityItem } from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel } from "../components/cards/Panel";
import { ScoreBar } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
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
  const tableRef = useRef<HTMLDivElement | null>(null);
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
  const selected = items.find((item) => item.id === selectedId) || items[0];
  const scrollRows = (direction: -1 | 1) => {
    const table = tableRef.current;
    if (!table) return;
    const step = Math.max(160, table.clientHeight - 56);
    table.scrollTo({ top: table.scrollTop + direction * step, behavior: "smooth" });
    table.focus({ preventScroll: true });
  };

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
          <label>Kind<select value={kind} onChange={(event) => setKind(event.target.value)}><option value="all">All</option><option value="instrument">Instruments</option><option value="model">Models</option></select></label>
          <label>Sort<select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="opportunity_score">Opportunity</option><option value="evidence_score">Evidence</option><option value="risk_score">Risk</option></select></label>
          <label>Min<input type="number" min={0} max={100} step={5} value={minScore} onChange={(event) => setMinScore(Number(event.target.value))} /></label>
          <label className="check"><input type="checkbox" checked={includeHold} onChange={(event) => setIncludeHold(event.target.checked)} /> Hold</label>
          <button type="submit">Refresh</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      <section className="radar-layout">
        <Panel title="Ranked Candidates" meta={`${items.length} shown${payload?.total_candidates ? ` · ${payload.total_candidates} screened` : ""}`}>
          {items.length === 0 ? (
            <EmptyState
              title="No eligible real rankings"
              body={payload?.required_action || "Upload or fetch real price history to unlock rankings."}
              actions={["No placeholder rows are rendered", "Sample data stays demo-only"]}
            />
          ) : (
            <>
              <div className="radar-scroll-controls" aria-label="Ranked candidate row controls">
                <span>{items.length} rows</span>
                <button type="button" onClick={() => scrollRows(-1)}>Scroll up</button>
                <button type="button" onClick={() => scrollRows(1)}>Scroll down</button>
              </div>
              <div ref={tableRef} className="terminal-table" tabIndex={0} aria-label="Scrollable ranked candidates table" onKeyDown={scrollTableByKey}>
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
            <EmptyState title="Selection locked" body={payload?.reason || "No eligible candidate is available."} />
          )}
        </Panel>
      </section>
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

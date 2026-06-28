import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ModelSummary, StrategyResponse, TickerSummary } from "../api/types";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { LineChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtAuto, fmtNumber, fmtPct, titleCase } from "../utils/format";

export function StrategyLab({
  tickers,
  models,
  selectedInstrument,
  selectedModel,
  onSelectInstrument,
  onSelectModel,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
}) {
  const defaultTarget = selectedModel ? `model:${selectedModel}` : selectedInstrument ? `instrument:${selectedInstrument}` : tickers[0] ? `instrument:${tickers[0].symbol}` : "";
  const [target, setTarget] = useState(defaultTarget);
  const [costBps, setCostBps] = useState(5);
  const [slippageBps, setSlippageBps] = useState(0);
  const [payload, setPayload] = useState<StrategyResponse | null>(null);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const run = async (requestedTarget = target || defaultTarget) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      if (kind === "model") onSelectModel(id);
      else onSelectInstrument(id);
      const result = kind === "model"
        ? await api.strategyModel(id, costBps, slippageBps)
        : await api.strategyInstrument(id, costBps, slippageBps);
      if (requestId !== requestSeq.current) return;
      setPayload(result);
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Strategy Lab failed.");
    }
  };

  useEffect(() => {
    if (!defaultTarget) return;
    setTarget(defaultTarget);
    void run(defaultTarget);
  }, [defaultTarget]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Strategy Lab</div><h1>No-lookahead signal evidence</h1><p>Runs the existing Helios signal against buy-and-hold after explicit costs and slippage.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void run(); }}>
          <label>Target<TerminalSelect ariaLabel="Strategy target" value={target} onChange={setTarget} options={options} /></label>
          <label>Cost bps<input type="number" min={0} max={500} value={costBps} onChange={(event) => setCostBps(Number(event.target.value))} /></label>
          <label>Slippage<input type="number" min={0} max={500} value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></label>
          <button type="submit">Run</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {payload && <DataQualityBanner payload={payload} />}
      {payload && !payload.eligible_for_real_research && (
        <div className="strategy-demo-warning" role="status">
          <strong>Demo strategy result — synthetic/sample data, not real market evidence.</strong>
          <span>{payload.required_action || payload.reason || "Replace demo history with live or uploaded data before using strategy evidence."}</span>
        </div>
      )}
      {payload && !payload.strategy ? (
        <EmptyState title={payload.display_label || "Strategy evidence blocked"} body={payload.reason || payload.required_action || "Upload real history before using strategy evidence."} />
      ) : payload ? (
        <>
          <section className="dashboard-grid three">
            <Panel title="Evidence Verdict" meta={payload.beat_benchmark ? "Beat benchmark" : "Did not beat"}>
              <div className={`verdict-chip ${payload.beat_benchmark ? "verdict-positive" : "verdict-warning"}`}>
                <b>Beat benchmark?</b>
                <span>{payload.beat_benchmark ? "Yes" : "No"}</span>
              </div>
              <p className="lead">{payload.beat_benchmark ? "Signal evidence beat buy-and-hold in this window." : "Signal evidence did not clear buy-and-hold in this window."}</p>
              <div className="metric-grid">
                <StatTile label="Strategy return" value={fmtPct(payload.strategy?.total_return_pct)} tone={(payload.strategy?.total_return_pct || 0) >= 0 ? "positive" : "negative"} />
                <StatTile label="Benchmark" value={fmtPct(payload.benchmark?.total_return_pct)} />
                <StatTile label="Sharpe" value={fmtNumber(payload.strategy?.sharpe, 2)} />
                <StatTile label="Max drawdown" value={fmtPct(payload.strategy?.max_drawdown_pct)} tone="negative" />
              </div>
            </Panel>
            <Panel title="Trade Statistics">
              <KeyValues data={payload.trade_stats || {}} />
            </Panel>
            <Panel title="Evidence Quality">
              <KeyValues data={{
                "Data mode": payload.display_label || payload.data_mode,
                "Eligible": payload.eligible_for_real_research,
                "Cost model": `${costBps} bps commission + ${slippageBps} bps slippage`,
              }} />
            </Panel>
          </section>
          <Panel title="Equity Curve" meta="strategy vs benchmark">
            <LineChart labels={payload.dates || []} series={[
              { label: "Signal strategy", values: payload.strategy_curve || [], tone: "positive" },
              { label: "Buy-and-hold", values: payload.benchmark_curve || [], tone: "neutral" },
            ]} />
          </Panel>
          <section className="dashboard-grid">
            <Panel title="Drawdown"><LineChart labels={payload.dates || []} series={[{ label: "Drawdown", values: payload.drawdown_curve || [], tone: "negative" }]} height={140} /></Panel>
            <Panel title="Rolling Sharpe"><LineChart labels={payload.dates || []} series={[{ label: "Rolling Sharpe", values: payload.rolling_sharpe_curve || [], tone: "warning" }]} height={140} /></Panel>
          </section>
          <section className="dashboard-grid">
            <Panel title="Assumptions, Costs, and Slippage">
              <KeyValues data={{
                ...(payload.assumptions || {}),
                "Commission cost": `${costBps} bps`,
                "Slippage": `${slippageBps} bps`,
              }} />
            </Panel>
            <Panel title="Methodology and Caveats">
              <KeyValues data={payload.methodology || { methodology: "Backend evidence model only" }} />
            </Panel>
          </section>
        </>
      ) : <EmptyState title="Select a target" body="Choose an instrument or model to run Strategy Lab." />}
    </div>
  );
}

function KeyValues({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="key-values">
      {Object.entries(data).map(([key, value]) => (
        <span key={key}><b>{formatStrategyValue(key, value)}</b><small>{formatStrategyLabel(key)}</small></span>
      ))}
    </div>
  );
}

function formatStrategyLabel(key: string): string {
  const labels: Record<string, string> = {
    avg_loss_pct: "Average loss",
    avg_win_pct: "Average win",
    completed_trades: "Completed trades",
    current_position: "Current position",
    exposure_pct: "Exposure",
    n_trades: "Trades",
    profit_factor: "Profit factor",
    turnover: "Turnover",
    win_rate_pct: "Win rate",
    entry_threshold: "Entry threshold",
    exit_threshold: "Exit threshold",
    round_trip_cost_bps: "Round-trip cost",
    slippage_bps: "Slippage",
  };
  return labels[key] || titleCase(key);
}

function formatStrategyValue(key: string, value: unknown): string {
  if (Array.isArray(value)) return value.length ? `${value.length} items` : "None";
  if (value && typeof value === "object") return "Details";
  if (typeof value === "number" && Number.isFinite(value)) {
    if (key.endsWith("_pct") || key.includes("return") || key.includes("drawdown")) return fmtPct(value);
    if (key.endsWith("_bps")) return `${fmtNumber(value, 0)} bps`;
    if (key === "profit_factor" || key === "sharpe") return fmtNumber(value, 2);
    return Number.isInteger(value) ? fmtNumber(value, 0) : fmtNumber(value, 2);
  }
  return fmtAuto(value);
}

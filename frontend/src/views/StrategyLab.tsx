import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ModelSummary, StrategyResponse, TickerSummary } from "../api/types";
import { AICopilotPanel, strategyCopilotActions } from "../components/ai/AICopilotPanel";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { ChartSummary, DrawdownChart, EquityCurveChart, RollingSharpeChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtAuto, fmtNumber, fmtPct, fmtTimestamp, titleCase } from "../utils/format";

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
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<StrategyResponse>({ failureMessage: "Strategy Lab failed." });
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);
  const selectedInstrumentMeta = useMemo(() => {
    const [kind, id] = (target || defaultTarget).split(":");
    if (kind !== "instrument") return null;
    return tickers.find((ticker) => ticker.symbol === id) || null;
  }, [defaultTarget, target, tickers]);
  const copilotPayload = useMemo<Record<string, unknown> | null>(() => {
    if (!payload) return null;
    return {
      strategy: payload,
      target,
      cost_bps: costBps,
      slippage_bps: slippageBps,
      instrument_source: selectedInstrumentMeta,
      disclaimer: payload.disclaimer,
    };
  }, [costBps, payload, selectedInstrumentMeta, slippageBps, target]);

  const run = useCallback((requestedTarget: string) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    if (kind === "model") onSelectModel(id);
    else onSelectInstrument(id);
    void load(requestedTarget, () => kind === "model"
      ? api.strategyModel(id, costBps, slippageBps)
      : api.strategyInstrument(id, costBps, slippageBps));
  }, [costBps, load, onSelectInstrument, onSelectModel, slippageBps]);

  useEffect(() => {
    if (!defaultTarget || isCurrentTarget(defaultTarget)) return;
    setTarget(defaultTarget);
    run(defaultTarget);
  }, [defaultTarget, isCurrentTarget, run]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Strategy Lab</div><h1>No-lookahead signal evidence</h1><p>Runs the existing Helios signal against buy-and-hold after explicit costs and slippage.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); run(target || defaultTarget); }}>
          <label>Target<TerminalSelect ariaLabel="Strategy target" value={target} onChange={setTarget} options={options} /></label>
          <label>Cost bps<input type="number" min={0} max={500} value={costBps} onChange={(event) => setCostBps(Number(event.target.value))} /></label>
          <label>Slippage<input type="number" min={0} max={500} value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /></label>
          <button type="submit">Run</button>
        </form>
      </header>
      {error && <div className="notice danger" role="alert">{error}</div>}
      {selectedInstrumentMeta && (
        <div className="source-context-strip">
          <span><b>Source</b>{selectedInstrumentMeta.source}</span>
          <span><b>Rows</b>{selectedInstrumentMeta.row_count ?? "—"}</span>
          <span><b>Date range</b>{selectedInstrumentMeta.first_date || "?"} to {selectedInstrumentMeta.last_date || "?"}</span>
          <span><b>Last refresh</b>{fmtTimestamp(selectedInstrumentMeta.last_refresh?.attempted_at)}</span>
        </div>
      )}
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
            <ChartSummary items={[
              { label: "Strategy return", value: fmtPct(payload.strategy?.total_return_pct), tone: (payload.strategy?.total_return_pct || 0) >= 0 ? "positive" : "negative" },
              { label: "Benchmark return", value: fmtPct(payload.benchmark?.total_return_pct), tone: (payload.benchmark?.total_return_pct || 0) >= 0 ? "positive" : "negative" },
              { label: "Strategy max drawdown", value: fmtPct(payload.strategy?.max_drawdown_pct), tone: "negative" },
              { label: "Exposure", value: fmtPct(Number(payload.trade_stats?.exposure_pct)), tone: "info" },
            ]} />
            <EquityCurveChart labels={payload.dates || []} strategy={payload.strategy_curve || []} benchmark={payload.benchmark_curve || []} />
          </Panel>
          <section className="dashboard-grid">
            <Panel title="Drawdown"><DrawdownChart labels={payload.dates || []} values={payload.drawdown_curve || []} height={160} /></Panel>
            <Panel title="Rolling Sharpe"><RollingSharpeChart labels={payload.dates || []} values={payload.rolling_sharpe_curve || []} height={160} /></Panel>
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
          <AICopilotPanel
            contextLabel={`${payload.name || "Strategy"} evidence`}
            payload={copilotPayload}
            dataMode={payload.data_mode}
            actions={strategyCopilotActions}
          />
        </>
      ) : isLoading ? (
        <div className="loading" role="status">Running no-lookahead strategy evidence...</div>
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

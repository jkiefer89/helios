import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { MandateSummary, ModelSummary, StrategyResponse, TickerSummary } from "../api/types";
import { AICopilotPanel, strategyCopilotActions } from "../components/ai/AICopilotPanel";
import { DataQualityBanner } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import { ChartSummary, DrawdownChart, EquityCurveChart, RollingSharpeChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { RequestStatus } from "../components/states/RequestStatus";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtAuto, fmtNumber, fmtPct, fmtTimestamp, titleCase } from "../utils/format";

export function StrategyLab({
  tickers,
  models,
  mandates,
  selectedInstrument,
  selectedModel,
  onSelectInstrument,
  onSelectModel,
}: {
  tickers: TickerSummary[];
  models: ModelSummary[];
  mandates: MandateSummary[];
  selectedInstrument?: string;
  selectedModel?: string;
  onSelectInstrument: (symbol: string) => void;
  onSelectModel: (id: string) => void;
}) {
  // No first-ticker fallback: strategy targets are operator-chosen, never a silent default.
  const defaultTarget = selectedModel ? `model:${selectedModel}` : selectedInstrument ? `instrument:${selectedInstrument}` : "";
  const [target, setTarget] = useState(defaultTarget);
  const [costBps, setCostBps] = useState(5);
  const [slippageBps, setSlippageBps] = useState(0);
  const { payload, failure, staleResult, isLoading, load, retry, isCurrentTarget } = useViewFetch<StrategyResponse>({ failureMessage: "Strategy Lab failed." });
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
  const freshness = payload?.freshness || selectedInstrumentMeta?.freshness;

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
      <RequestStatus failure={failure} stale={staleResult} onRetry={retry} />
      {selectedInstrumentMeta && (
        <div className="source-context-strip">
          <span><b>Source</b>{selectedInstrumentMeta.source}</span>
          <span><b>Rows</b>{freshness?.row_count ?? selectedInstrumentMeta.row_count ?? "—"}</span>
          <span><b>Date range</b>{freshness?.first_bar_date || selectedInstrumentMeta.first_date || "?"} to {freshness?.latest_bar_date || selectedInstrumentMeta.last_date || "?"}</span>
          <span><b>Retrieval evidence</b>{freshness?.retrieved_at ? fmtTimestamp(freshness.retrieved_at) : titleCase(freshness?.status || "not verified")}</span>
        </div>
      )}
      {payload && <DataQualityBanner payload={payload} />}
      {payload && !payload.eligible_for_real_research && (
        <div className="strategy-blocked-warning" role="status">
          <strong>Strategy evidence blocked - eligible market history required.</strong>
          <span>{payload.required_action || payload.reason || "Provide live or uploaded data before using strategy evidence."}</span>
        </div>
      )}
      {payload && (
        <ResearchContextPanel
          payload={payload}
          target={target || defaultTarget}
          mandates={mandates}
          onSaved={() => run(target || defaultTarget)}
        />
      )}
      {payload && !payload.strategy ? (
        <EmptyState title={payload.display_label || "Strategy evidence blocked"} body={payload.reason || payload.required_action || "Upload real history before using strategy evidence."} />
      ) : payload ? (
        <>
          <section className="dashboard-grid three">
            <Panel title="Evidence Verdict" meta={formatActionLabel(payload.current_signal?.action_label || "action unavailable")}>
              <div className="strategy-action-line">
                <span className="strategy-action-label">{formatActionLabel(payload.current_signal?.action_label || "Unavailable")}</span>
                <small>effective {titleCase(payload.current_signal?.effective_session || "unavailable")}</small>
              </div>
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
                <StatTile label="Threshold score" value={fmtNumber(payload.current_signal?.score, 2)} />
                <StatTile label="Observed position" value={titleCase(payload.current_signal?.signal_state || "Unavailable")} />
              </div>
              {payload.current_signal?.basis && <p className="forecast-note">{payload.current_signal.basis}</p>}
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
            <PathEvidencePanel payload={payload} />
            <OosEvidencePanel payload={payload} />
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

function ResearchContextPanel({
  payload,
  target,
  mandates,
  onSaved,
}: {
  payload: StrategyResponse;
  target: string;
  mandates: MandateSummary[];
  onSaved: () => void;
}) {
  const context = payload.research_context;
  const isInstrument = target.startsWith("instrument:");
  const [editing, setEditing] = useState(isInstrument && !context?.configured);
  const [thesis, setThesis] = useState(context?.thesis || "");
  const [mandateKey, setMandateKey] = useState(context?.mandate_key || "");
  const [benchmark, setBenchmark] = useState(context?.benchmark || "");
  const [horizon, setHorizon] = useState(context?.horizon_days ? String(context.horizon_days) : "");
  const [criteria, setCriteria] = useState((context?.invalidation_criteria || []).join("\n"));
  const [changeNote, setChangeNote] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    setThesis(context?.thesis || "");
    setMandateKey(context?.mandate_key || "");
    setBenchmark(context?.benchmark || "");
    setHorizon(context?.horizon_days ? String(context.horizon_days) : "");
    setCriteria((context?.invalidation_criteria || []).join("\n"));
    setChangeNote("");
    setState("idle");
    setMessage("");
    setEditing(isInstrument && !context?.configured);
  }, [context, isInstrument, target]);

  if (!isInstrument) {
    return (
      <Panel title="Governed Research Context" meta={context?.configured ? "Model thesis active" : "Thesis required"}>
        {context?.configured ? (
          <>
            <p className="lead">{context.thesis}</p>
            <KeyValues data={{
              Mandate: context.mandate_label || context.mandate_key,
              Benchmark: context.benchmark,
              "Governance source": context.governance_source,
            }} />
          </>
        ) : (
          <div className="notice warning">This model has no governed thesis. Add its thesis through the model analysis workspace before relying on narrative review.</div>
        )}
      </Panel>
    );
  }

  if (!editing && context?.configured) {
    return (
      <Panel title="Governed Research Context" meta={`Version ${context.version ?? "—"}`}>
        <p className="lead">{context.thesis}</p>
        <KeyValues data={{
          Mandate: context.mandate_label || context.mandate_key,
          Benchmark: context.benchmark,
          "Research horizon": context.horizon_days ? `${context.horizon_days} sessions` : null,
          "Last changed": context.created_at ? fmtTimestamp(context.created_at) : null,
        }} />
        {!!context.invalidation_criteria?.length && (
          <div className="strategy-context-criteria">
            <b>Invalidation criteria</b>
            <ul>{context.invalidation_criteria.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        )}
        <button type="button" onClick={() => setEditing(true)}>Edit governed context</button>
        {state === "saved" && <p className="forecast-note">{message}</p>}
      </Panel>
    );
  }

  const save = async () => {
    const parsedHorizon = Number(horizon);
    setState("saving");
    setMessage("");
    try {
      await api.saveResearchContext({
        target_kind: "instrument",
        target_id: target.split(":")[1] || "",
        thesis: thesis.trim(),
        mandate_key: mandateKey,
        benchmark: benchmark.trim(),
        horizon_days: parsedHorizon,
        invalidation_criteria: criteria.split("\n").map((item) => item.trim()).filter(Boolean),
        change_note: changeNote.trim(),
      });
      setState("saved");
      setMessage("Governed research context saved with a new evidence version.");
      setEditing(false);
      onSaved();
    } catch (error) {
      setState("error");
      setMessage(error instanceof Error ? error.message : "Could not save research context.");
    }
  };

  return (
    <Panel title="Governed Research Context" meta={context?.configured ? `Version ${context.version ?? "—"}` : "Required for thesis-aware review"}>
      <div className="decision-form strategy-context-form">
        <label className="decision-form__field">Investment thesis
          <textarea
            value={thesis}
            rows={3}
            maxLength={2000}
            onChange={(event) => setThesis(event.currentTarget.value)}
            placeholder="State the intended use, expected return driver, and risk the position is meant to accept."
          />
        </label>
        <div className="strategy-context-grid">
          <label className="decision-form__field">Mandate
            <TerminalSelect
              ariaLabel="Research mandate"
              value={mandateKey}
              onChange={setMandateKey}
              options={mandates.map((item) => ({ value: item.key, label: item.label }))}
              placeholder="Select mandate"
            />
          </label>
          <label className="decision-form__field">Benchmark
            <input value={benchmark} maxLength={20} onChange={(event) => setBenchmark(event.currentTarget.value)} />
          </label>
          <label className="decision-form__field">Horizon, sessions
            <input type="number" min={5} max={252} value={horizon} onChange={(event) => setHorizon(event.currentTarget.value)} />
          </label>
        </div>
        <label className="decision-form__field">Invalidation criteria, one per line
          <textarea value={criteria} rows={3} onChange={(event) => setCriteria(event.currentTarget.value)} />
        </label>
        <label className="decision-form__field">Required change note
          <input value={changeNote} maxLength={800} onChange={(event) => setChangeNote(event.currentTarget.value)} />
        </label>
        <p className="forecast-note">This context may be included in sanitized AI review. Do not enter client identities or confidential account details.</p>
        <div className="decision-form__row">
          <button
            type="button"
            disabled={state === "saving" || changeNote.trim().length < 5}
            onClick={() => void save()}
          >{state === "saving" ? "Saving…" : "Save governed context"}</button>
          {context?.configured && <button type="button" onClick={() => setEditing(false)}>Cancel</button>}
        </div>
        {state === "error" && <div className="notice danger" role="alert">{message}</div>}
      </div>
    </Panel>
  );
}

function PathEvidencePanel({ payload }: { payload: StrategyResponse }) {
  const path = payload.path_evidence;
  const best = path?.trade_summary?.best_trade;
  const worst = path?.trade_summary?.worst_trade;
  const deepest = path?.drawdown_summary?.deepest_episodes?.[0];
  return (
    <Panel title="Path Evidence" meta="derived summaries; full curves stay local">
      <KeyValues data={{
        "Completed trades": path?.trade_summary?.completed_count,
        "Winning trades": path?.trade_summary?.winning_count,
        "Best trade": best ? `${best.entry_date} to ${best.exit_date || "open"} · ${fmtPct(best.net_return_pct)}` : null,
        "Worst trade": worst ? `${worst.entry_date} to ${worst.exit_date || "open"} · ${fmtPct(worst.net_return_pct)}` : null,
        "Deepest drawdown": deepest ? `${deepest.peak_date} to ${deepest.trough_date} · ${fmtPct(deepest.depth_pct)}` : fmtPct(path?.drawdown_summary?.maximum_drawdown_pct),
        "Rolling Sharpe latest": fmtNumber(path?.rolling_sharpe_summary?.latest, 2),
        "Negative Sharpe windows": fmtPct(path?.rolling_sharpe_summary?.negative_window_pct),
      }} />
      {path?.privacy_basis && <p className="forecast-note">{path.privacy_basis}</p>}
    </Panel>
  );
}

function OosEvidencePanel({ payload }: { payload: StrategyResponse }) {
  const oos = payload.oos_evidence;
  if (!oos || oos.status !== "ok" || !oos.primary) {
    return (
      <Panel title="Walk-Forward Evidence" meta="out-of-sample">
        <div className="notice warning">{oos?.reason || "Out-of-sample evidence is unavailable for this history."}</div>
      </Panel>
    );
  }
  return (
    <Panel title="Walk-Forward Evidence" meta={`${oos.fold_count} non-overlapping folds`}>
      <KeyValues data={{
        "OOS strategy return": fmtPct(oos.primary.strategy_return_pct),
        "OOS benchmark": fmtPct(oos.primary.benchmark_return_pct),
        "OOS net excess": fmtPct(oos.primary.net_excess_return_pct),
        "Benchmark-beating folds": fmtPct(oos.primary.benchmark_beating_fold_pct),
        "Primary sensitivity rank": oos.sensitivity ? `${oos.sensitivity.primary_rank_by_net_excess} of ${oos.sensitivity.variant_count}` : null,
        "Positive nearby variants": oos.sensitivity ? `${oos.sensitivity.positive_excess_variant_count} of ${oos.sensitivity.variant_count}` : null,
      }} />
      {oos.sensitivity && (
        <div className="terminal-table terminal-data-table-shell strategy-oos-table" tabIndex={0} aria-label="Threshold sensitivity evidence">
          <table className="terminal-data-table">
            <thead><tr><th>Entry</th><th>Exit</th><th>OOS return</th><th>Net excess</th><th>Beat folds</th></tr></thead>
            <tbody>{oos.sensitivity.variants.map((variant) => (
              <tr key={`${variant.entry_threshold}:${variant.exit_threshold}`} className={variant.primary ? "selected" : ""}>
                <td>{fmtNumber(variant.entry_threshold, 2)}{variant.primary ? " · primary" : ""}</td>
                <td>{fmtNumber(variant.exit_threshold, 2)}</td>
                <td>{fmtPct(variant.strategy_return_pct)}</td>
                <td>{fmtPct(variant.net_excess_return_pct)}</td>
                <td>{fmtPct(variant.benchmark_beating_fold_pct)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <p className="forecast-note">The 0.15/-0.05 primary rule is fixed before evaluation. Nearby variants diagnose fragility; Helios does not select a winner from test results.</p>
    </Panel>
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

function formatActionLabel(value: string): string {
  return titleCase(value.toLowerCase());
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

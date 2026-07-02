import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  AnalysisHolding,
  AnalysisInsight,
  AnalysisMandate,
  AnalysisResponse,
  AnalysisSeries,
  AnalysisSignal,
  BacktestPayload,
  DataMode,
  ForecastBands,
  LongHorizonForecast,
  MetricSet,
  ModelSummary,
  ProvenancePayload,
  SentimentPayload,
  TacticalForecast,
  TickerSummary,
} from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import {
  ChartSummary,
  DrawdownChart,
  EquityCurveChart,
  ForecastConeChart,
  HistogramChart,
  MacdChart,
  PriceTrendChart,
  RsiChart,
} from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { fmtAuto, fmtMoney, fmtNumber, fmtPct, titleCase } from "../utils/format";

export function Analysis({
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
  const [horizon, setHorizon] = useState<string | number>(21);
  const [payload, setPayload] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState("");
  const requestSeq = useRef(0);
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);

  const load = async (requestedTarget = target || defaultTarget) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    try {
      setError("");
      setPayload(null);
      if (kind === "model") {
        onSelectModel(id);
        const result = await api.analyzeModel(id, horizon);
        if (requestId !== requestSeq.current) return;
        setPayload(result);
      } else {
        onSelectInstrument(id);
        const result = await api.analyzeInstrument(id, Number(horizon) || 21);
        if (requestId !== requestSeq.current) return;
        setPayload(result);
      }
    } catch (err) {
      if (requestId !== requestSeq.current) return;
      setPayload(null);
      setError(err instanceof Error ? err.message : "Analysis failed.");
    }
  };

  useEffect(() => {
    if (!defaultTarget) return;
    setTarget(defaultTarget);
    void load(defaultTarget);
  }, [defaultTarget]);

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Analysis</div><h1>Instrument and model detail</h1><p>Legacy analytics payloads rendered in the React terminal.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); void load(); }}>
          <label>Target<TerminalSelect ariaLabel="Analysis target" value={target} onChange={setTarget} options={options} /></label>
          <label>Horizon<input value={horizon} onChange={(event) => setHorizon(event.target.value)} /></label>
          <button type="submit">Analyze</button>
        </form>
      </header>
      {error && <div className="notice danger">{error}</div>}
      {!payload ? <EmptyState title="Select a target" body="Choose an instrument or model to load analytics." /> : (
        <AnalysisPayload payload={payload} />
      )}
    </div>
  );
}

function AnalysisPayload({ payload }: { payload: AnalysisResponse }) {
  const quality = analysisQuality(payload);
  const eligible = quality.eligible_for_real_research === true;
  const actionClass = eligible ? safeAction(payload.signal.action) : "preview";
  const signalLabel = eligible ? payload.signal.action : "PREVIEW";
  const panelSuffix = eligible ? "" : " preview";
  const dailyReturns = pctReturns(payload.series.close);
  const drawdown = drawdownSeries(payload.series.close);
  const tactical = payload.forecast.kind === "long" ? payload.forecast_short : payload.forecast;
  return (
    <>
      <DataQualityBanner payload={quality} />
      <Panel title={`${payload.name}${eligible ? "" : " preview"}`} meta={payload.source ? <SourcePill source={payload.source} /> : payload.mandate?.label}>
        <div className={`signal-strip ${eligible ? "" : "signal-strip--preview"}`}>
          <strong className={`signal-action action-${actionClass}`}>{signalLabel}</strong>
          <p>{payload.signal.headline_rationale || payload.signal.rationale}</p>
          <span>{eligible ? `${fmtNumber(payload.signal.conviction_pct, 0)}% conviction` : "Research locked"}</span>
        </div>
        {!eligible && <div className="warning-list"><span>{quality.required_action || "Replace demo or mixed inputs before treating this as research evidence."}</span></div>}
        {payload.signal.caveats?.length ? <div className="warning-list">{payload.signal.caveats.map((caveat) => <span key={caveat}>{caveat}</span>)}</div> : null}
      </Panel>
      <section className="dashboard-grid three">
        <Panel title={`Metrics${panelSuffix}`}>
          <div className="metric-grid">
            {Object.entries(payload.metrics).slice(0, 8).map(([key, value]) => (
              <StatTile key={key} label={titleCase(key)} value={fmtAuto(value)} />
            ))}
          </div>
        </Panel>
        <SentimentPanel sentiment={payload.sentiment} suffix={panelSuffix} />
        <SignalBreakdownPanel signal={payload.signal} forecast={tactical} suffix={panelSuffix} />
      </section>
      <section className="dashboard-grid">
        <Panel title={`Price, Trend & Bollinger Bands${panelSuffix}`} className="span-2" meta={markerNote(payload.series)}>
          <PriceTrendChart
            labels={payload.series.dates}
            close={payload.series.close}
            sma50={payload.series.sma50 || []}
            sma200={payload.series.sma200 || []}
            bbUpper={payload.series.bb_upper || []}
            bbLower={payload.series.bb_lower || []}
            markers={payload.series.markers || []}
          />
        </Panel>
        <Panel title={`RSI${panelSuffix}`} meta="70 overbought · 30 oversold">
          <RsiChart labels={payload.series.dates} values={payload.series.rsi || []} />
        </Panel>
        <Panel title={`MACD${panelSuffix}`}>
          <MacdChart
            labels={payload.series.dates}
            macd={payload.series.macd || []}
            signal={payload.series.macd_signal || []}
            hist={payload.series.macd_hist || []}
          />
        </Panel>
        <Panel title={`Drawdown${panelSuffix}`}>
          <DrawdownChart labels={payload.series.dates} values={drawdown} height={160} />
        </Panel>
        <Panel title={`Daily Return Distribution${panelSuffix}`}>
          <HistogramChart values={dailyReturns} label="Daily return %" buckets={9} tone={eligible ? "info" : "warning"} />
        </Panel>
      </section>
      <section className="dashboard-grid">
        <ForecastPanel payload={payload} suffix={panelSuffix} />
        {payload.mandate && <MandateFitPanel metrics={payload.metrics} mandate={payload.mandate} signal={payload.signal} suffix={panelSuffix} />}
        {payload.insights && <InsightsPanel insights={payload.insights} suffix={panelSuffix} />}
      </section>
      <section className="dashboard-grid">
        <BacktestPanel backtest={payload.backtest} suffix={panelSuffix} />
      </section>
      {payload.holdings && (
        <Panel title={`Holdings${panelSuffix}`}>
          <div className="holdings-table">
            {payload.holdings.map((holding: AnalysisHolding) => (
              <div key={holding.ticker}>
                <strong>{holding.ticker}</strong>
                <span>{fmtPct(holding.weight * 100)}</span>
                <span>{holding.source || "unavailable"}</span>
                <span>{holding.signal || "—"}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}

function ForecastPanel({ payload, suffix }: { payload: AnalysisResponse; suffix: string }) {
  const forecast = payload.forecast;
  if (forecast.kind === "long") {
    return <LongForecastPanel forecast={forecast} mandate={payload.mandate} suffix={suffix} />;
  }
  return <TacticalForecastPanel forecast={forecast} series={payload.series} suffix={suffix} />;
}

function TacticalForecastPanel({ forecast, series, suffix }: { forecast: TacticalForecast; series: AnalysisSeries; suffix: string }) {
  const accuracy = forecast.quality?.directional_accuracy;
  return (
    <Panel
      title={`Return Forecast & Confidence Cone${suffix}`}
      className="span-2"
      meta={`${forecast.horizon_days}d horizon · p(up) ${fmtNumber(forecast.prob_up * 100, 0)}%`}
    >
      <ChartSummary items={[
        { label: "Expected return", value: fmtPct(forecast.expected_return_pct), tone: signTone(forecast.expected_return_pct) },
        { label: "Annualized drift", value: fmtPct(forecast.annualized_drift_pct), tone: signTone(forecast.annualized_drift_pct) },
        { label: "Expected vol", value: `${fmtNumber(forecast.expected_vol_pct, 1)}%` },
        { label: "Dir. accuracy", value: accuracy == null ? "—" : `${fmtNumber(accuracy * 100, 1)}%` },
      ]} />
      <ForecastConeChart points={tacticalConePoints(series, forecast)} />
    </Panel>
  );
}

function LongForecastPanel({ forecast, mandate, suffix }: { forecast: LongHorizonForecast; mandate?: AnalysisMandate; suffix: string }) {
  const cagr = forecast.cagr_pct || {};
  const terminal = forecast.terminal || {};
  const params = forecast.params || {};
  const breachProb = forecast.prob_breach_maxdd;
  const tolerance = mandate?.max_drawdown_tolerance_pct;
  return (
    <Panel
      title={`${forecast.label} Strategic Value Projection ($10,000 base)${suffix}`}
      className="span-2"
      meta={`drift ${fmtPct(params.mu_long_pct)}/yr (λ${fmtNumber(params.anchor_weight_lambda, 2)} to anchor) · vol ${fmtNumber(params.sigma_eff_pct, 1)}%`}
    >
      <ChartSummary items={[
        { label: "Median value", value: fmtMoney(terminal.p50) },
        { label: "Median CAGR", value: fmtPct(cagr.p50), tone: signTone(cagr.p50) },
        { label: "P05–P95 CAGR", value: `${fmtNumber(cagr.p05, 1)}% … ${fmtNumber(cagr.p95, 1)}%` },
        { label: "Prob. positive", value: forecast.prob_positive == null ? "—" : `${fmtNumber(forecast.prob_positive * 100, 0)}%` },
        { label: `Meets ${fmtNumber(forecast.mandate_target_pct, 1)}% target`, value: forecast.prob_meets_mandate == null ? "—" : `${fmtNumber(forecast.prob_meets_mandate * 100, 0)}%` },
        { label: "Median path drawdown", value: `${fmtNumber(forecast.drawdown_median_pct, 0)}%`, tone: signTone(forecast.drawdown_median_pct) },
        { label: tolerance != null ? `Breach −${fmtNumber(tolerance, 0)}% tolerance` : "Breach drawdown tolerance", value: breachProb == null ? "—" : `${fmtNumber(breachProb * 100, 0)}%`, tone: breachProb == null ? "neutral" : breachProb > 0.2 ? "negative" : "positive" },
        { label: "Value range", value: `${fmtMoney(terminal.p05)} – ${fmtMoney(terminal.p95)}` },
      ]} />
      <ForecastConeChart points={longConePoints(forecast)} baseline={forecast.base_value} />
      {forecast.disclaimer && <p className="muted">{forecast.disclaimer}</p>}
    </Panel>
  );
}

function SentimentPanel({ sentiment, suffix }: { sentiment?: SentimentPayload; suffix: string }) {
  const label = sentiment?.aggregate_label || "neutral";
  const tone = label === "positive" ? "positive" : label === "negative" ? "negative" : "neutral";
  return (
    <Panel
      title={`News Sentiment${suffix}`}
      meta={sentiment ? <span className={`badge tone-${tone}`}>{label} ({fmtSigned(sentiment.aggregate_score, 2)})</span> : undefined}
    >
      {!sentiment || !sentiment.items.length ? (
        <p className="muted">No headlines available for this target.</p>
      ) : (
        <div>
          {sentiment.items.map((item, index) => (
            <div className="queue-line" key={`${item.headline}-${index}`}>
              <span>{item.label}</span>
              <strong>{item.headline}</strong>
              <p>sentiment score {fmtSigned(item.score, 2)}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function SignalBreakdownPanel({ signal, forecast, suffix }: { signal: AnalysisSignal; forecast?: TacticalForecast; suffix: string }) {
  const components = signal.components || [];
  const featureWeights = (forecast?.feature_weights || []).slice(0, 6);
  const maxAbs = Math.max(...components.map((component) => Math.abs(component.contribution)), 0.25);
  const clauses = [...components]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .filter((component) => component.clause);
  const mandateFit = signal.mandate_fit;
  return (
    <Panel
      title={`Signal Component Breakdown${suffix}`}
      meta={mandateFit != null && mandateFit < 1 ? `conviction scaled ×${fmtNumber(mandateFit, 2)} for mandate fit` : undefined}
    >
      {!components.length ? (
        <p className="muted">Component attribution appears when the composite signal is available.</p>
      ) : (
        <>
          <div className="mini-bars">
            {components.map((component) => (
              <div className="mini-bars__row" key={component.name}>
                <span>{titleCase(component.name)} · {fmtNumber(component.effective_weight * 100, 0)}% wt</span>
                <div>
                  <i
                    className={component.contribution >= 0 ? "tone-positive" : "tone-negative"}
                    style={{ width: `${Math.min(100, (Math.abs(component.contribution) / maxAbs) * 100)}%` }}
                  />
                </div>
                <b>{fmtSigned(component.contribution, 2)}</b>
              </div>
            ))}
          </div>
          {clauses.map((component) => <p className="muted" key={`clause-${component.name}`}>{component.clause}</p>)}
        </>
      )}
      {featureWeights.length > 0 && (
        <ChartSummary items={featureWeights.map((feature) => ({ label: feature.feature, value: fmtSigned(feature.weight, 3) }))} />
      )}
    </Panel>
  );
}

function MandateFitPanel({ metrics, mandate, signal, suffix }: { metrics: MetricSet; mandate: AnalysisMandate; signal: AnalysisSignal; suffix: string }) {
  const vol = asNumber(metrics.annual_vol_pct);
  const targetVol = mandate.target_vol_pct;
  const dd = asNumber(metrics.max_drawdown_pct);
  const tolerance = mandate.max_drawdown_tolerance_pct;
  const annualReturn = asNumber(metrics.annual_return_pct);
  const targetReturn = mandate.target_return_pct;
  const rows: Array<{ label: string; value: string; width: number; ok: boolean }> = [];
  if (vol != null && targetVol) {
    rows.push({
      label: `Volatility vs ≤${fmtNumber(targetVol, 0)}% target`,
      value: `${fmtNumber(vol, 1)}%`,
      width: Math.min((vol / (targetVol * 2)) * 100, 100),
      ok: vol <= targetVol * 1.15,
    });
  }
  if (dd != null && tolerance) {
    const drawdownAbs = Math.abs(dd);
    rows.push({
      label: `Max drawdown vs −${fmtNumber(tolerance, 0)}% tolerance`,
      value: `−${fmtNumber(drawdownAbs, 1)}%`,
      width: Math.min((drawdownAbs / (tolerance * 2)) * 100, 100),
      ok: drawdownAbs <= tolerance,
    });
  }
  if (annualReturn != null && targetReturn != null) {
    rows.push({
      label: `Return vs ${fmtNumber(targetReturn, 1)}% mandate target`,
      value: fmtPct(annualReturn),
      width: Math.min((Math.max(annualReturn, 0) / Math.max(targetReturn * 2, 1)) * 100, 100),
      ok: annualReturn >= targetReturn,
    });
  }
  return (
    <Panel
      title={`Mandate Fit${suffix}`}
      meta={signal.mandate_fit != null ? `signal conviction scaled ×${fmtNumber(signal.mandate_fit, 2)} for mandate fit` : undefined}
    >
      {!rows.length ? (
        <p className="muted">Mandate-fit bars appear when mandate risk budgets are available.</p>
      ) : (
        <div className="mini-bars">
          {rows.map((row) => (
            <div className="mini-bars__row" key={row.label}>
              <span>{row.label}</span>
              <div><i className={row.ok ? "tone-positive" : "tone-negative"} style={{ width: `${row.width}%` }} /></div>
              <b>{row.value}</b>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function InsightsPanel({ insights, suffix }: { insights: AnalysisInsight[]; suffix: string }) {
  return (
    <Panel
      title={`Model Insights${suffix}`}
      meta={insights.length ? `${insights.length} finding${insights.length > 1 ? "s" : ""}` : "no issues flagged"}
    >
      {!insights.length ? (
        <p className="muted">No mandate, concentration, or risk issues flagged for this model.</p>
      ) : (
        <div>
          {insights.map((insight, index) => (
            <div className="queue-line" key={`${insight.id}-${index}`}>
              <span>{insight.severity}</span>
              <strong>{titleCase(insight.category)}: {insight.message}</strong>
              <p>{insight.suggested_action}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function BacktestPanel({ backtest, suffix }: { backtest: BacktestPayload; suffix: string }) {
  const strategy = backtest.strategy || {};
  const benchmark = backtest.benchmark || {};
  const strategyVol = asNumber(strategy.annual_vol_pct);
  const benchmarkVol = asNumber(benchmark.annual_vol_pct);
  const volReduction = strategyVol != null && benchmarkVol != null ? benchmarkVol - strategyVol : null;
  return (
    <Panel title={`Signal Backtest vs Buy & Hold${suffix}`} className="span-2">
      <ChartSummary items={[
        { label: "Strategy return", value: fmtPct(strategy.total_return_pct), tone: signTone(strategy.total_return_pct) },
        { label: "Buy & hold", value: fmtPct(benchmark.total_return_pct), tone: signTone(benchmark.total_return_pct) },
        { label: "Strategy Sharpe", value: fmtNumber(strategy.sharpe, 2) },
        { label: "Strategy max DD", value: `${fmtNumber(strategy.max_drawdown_pct, 1)}%` },
        { label: "Win rate", value: `${fmtNumber(backtest.win_rate_pct, 0)}%` },
        { label: "Trades", value: String(backtest.n_trades ?? "—") },
        { label: "Time in market", value: `${fmtNumber(backtest.exposure_pct, 0)}%` },
        { label: "Vol reduction", value: volReduction == null ? "—" : `${fmtNumber(volReduction, 1)}%`, tone: signTone(volReduction ?? undefined) },
      ]} />
      <EquityCurveChart labels={backtest.dates || []} strategy={backtest.strategy_curve || []} benchmark={backtest.benchmark_curve || []} height={200} />
    </Panel>
  );
}

type ConePoint = {
  date: string;
  expected?: number | null;
  low?: number | null;
  high?: number | null;
  innerLow?: number | null;
  innerHigh?: number | null;
  history?: number | null;
};

function tacticalConePoints(series: AnalysisSeries, forecast: TacticalForecast): ConePoint[] {
  const tail = 60;
  const dates = series.dates.slice(-tail);
  const close = series.close.slice(-tail);
  const points: ConePoint[] = dates.map((date, index) => ({ date, history: close[index] ?? null }));
  const anchor = [...close].reverse().find((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (points.length && typeof anchor === "number") {
    points[points.length - 1] = {
      ...points[points.length - 1],
      expected: anchor,
      low: anchor,
      high: anchor,
      innerLow: anchor,
      innerHigh: anchor,
    };
  }
  const bands: Partial<ForecastBands> = forecast.bands || {};
  (forecast.dates || []).forEach((date, index) => {
    points.push({
      date,
      expected: bands.p50?.[index] ?? null,
      low: bands.p05?.[index] ?? null,
      high: bands.p95?.[index] ?? null,
      innerLow: bands.p25?.[index] ?? null,
      innerHigh: bands.p75?.[index] ?? null,
    });
  });
  return points;
}

function longConePoints(forecast: LongHorizonForecast): ConePoint[] {
  const base = forecast.base_value;
  const bands: Partial<ForecastBands> = forecast.bands || {};
  return [
    { date: "Now", expected: base, low: base, high: base, innerLow: base, innerHigh: base },
    ...(forecast.dates || []).map((date, index) => ({
      date,
      expected: bands.p50?.[index] ?? null,
      low: bands.p05?.[index] ?? null,
      high: bands.p95?.[index] ?? null,
      innerLow: bands.p25?.[index] ?? null,
      innerHigh: bands.p75?.[index] ?? null,
    })),
  ];
}

function markerNote(series: AnalysisSeries): string | undefined {
  const markers = series.markers || [];
  if (!markers.length) return undefined;
  const buys = markers.filter((marker) => marker.type === "buy").length;
  return `${buys} buy · ${markers.length - buys} sell signals`;
}

function signTone(value?: number): "positive" | "negative" | "neutral" {
  if (typeof value !== "number" || !Number.isFinite(value)) return "neutral";
  return value >= 0 ? "positive" : "negative";
}

function fmtSigned(value: unknown, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function safeAction(action?: string) {
  const normalized = String(action || "").toLowerCase();
  if (normalized === "buy" || normalized === "sell" || normalized === "hold" || normalized === "review") return normalized;
  return "review";
}

function analysisQuality(payload: AnalysisResponse): ProvenancePayload {
  if (payload.source) {
    const real = ["live", "upload"].includes(payload.source);
    return {
      data_mode: real ? "real" : "demo",
      display_label: real ? "Real/Uploaded Analysis Data" : "Demo Analysis Data",
      eligible_for_real_research: real,
      reason: real
        ? "This analysis uses a live or uploaded price history."
        : "This view is using bundled sample data for workflow demonstration only.",
      required_action: real ? "" : "Fetch live data or upload client price history before treating this as research evidence.",
      data_provenance: {
        source_counts: { [payload.source]: 1 },
        data_mode: real ? "real" : "demo",
      },
    };
  }

  const provenance = (payload.provenance || {}) as Record<string, unknown>;
  const simulatedWeight = Number(provenance.simulated_weight_pct || 0);
  const sampleCount = Number(provenance.n_sample || 0);
  const excludedCount = Number(provenance.n_excluded || 0);
  const real = simulatedWeight === 0 && sampleCount === 0 && excludedCount === 0;
  const mode: DataMode = real ? "real" : "mixed";
  return {
    data_mode: mode,
    display_label: real ? "Real Model Analysis Data" : "Mixed Model Analysis Data",
    eligible_for_real_research: real,
    reason: real
      ? "Model analysis is based on resolved live or uploaded holding histories."
      : "Model analysis includes sample, simulated, or excluded holding history and requires verification.",
    required_action: real ? "" : "Use Portfolio Clinic and reports only after replacing missing or simulated holding history.",
    data_provenance: {
      ...provenance,
      data_mode: mode,
    },
  };
}

function pctReturns(values: Array<number | null>): Array<number | null> {
  return values.map((value, index) => {
    const previous = values[index - 1];
    if (typeof value !== "number" || typeof previous !== "number" || !Number.isFinite(value) || !Number.isFinite(previous) || previous === 0) return null;
    return ((value / previous) - 1) * 100;
  });
}

function drawdownSeries(values: Array<number | null>): Array<number | null> {
  let peak = 0;
  return values.map((value) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return null;
    peak = Math.max(peak || value, value);
    return peak > 0 ? ((value / peak) - 1) * 100 : 0;
  });
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type {
  AnalysisForecast,
  AnalysisMandate,
  AnalysisResponse,
  AnalysisSignal,
  DataMode,
  LongForecast,
  MetricSet,
  ModelInsight,
  ModelSummary,
  ProvenancePayload,
  SentimentPayload,
  TacticalForecast,
  TickerSummary,
} from "../api/types";
import { DataQualityBanner, SourcePill } from "../components/badges/DataModeBadge";
import { Panel, StatTile } from "../components/cards/Panel";
import type { ForecastConePoint } from "../components/charts/adapters/forecastCone";
import { DrawdownChart, EquityCurveChart, ForecastConeChart, HistogramChart, MacdChart, PriceTrendChart, RsiChart } from "../components/charts/Charts";
import { EmptyState } from "../components/empty-states/EmptyState";
import { TerminalSelect } from "../components/forms/TerminalSelect";
import { useViewFetch } from "../hooks/useViewFetch";
import { fmtAuto, fmtMoney, fmtNumber, fmtPct, titleCase } from "../utils/format";

const LONG_HORIZON_PRESETS = ["6M", "1Y", "3Y", "5Y"] as const;

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
  const { payload, error, isLoading, load, isCurrentTarget } = useViewFetch<AnalysisResponse>({ failureMessage: "Analysis failed." });
  const options = useMemo(() => [
    ...tickers.map((ticker) => ({ value: `instrument:${ticker.symbol}`, label: `${ticker.symbol} · ${ticker.name}` })),
    ...models.map((model) => ({ value: `model:${model.id}`, label: `${model.name} · model` })),
  ], [tickers, models]);
  const targetIsModel = (target || defaultTarget).startsWith("model:");
  // Only trust availability once a model payload for the current target exists.
  const availableLong = payload?.horizon && targetIsModel ? payload.horizon.available_long : targetIsModel ? [...LONG_HORIZON_PRESETS] : [];

  const runAnalysis = useCallback((requestedTarget: string, requestedHorizon: string | number) => {
    const [kind, id] = requestedTarget.split(":");
    if (!kind || !id) return;
    if (kind === "model") {
      onSelectModel(id);
      void load(requestedTarget, () => api.analyzeModel(id, requestedHorizon), (result) => {
        // The backend may downgrade an unavailable long preset to a 21d tactical signal.
        if (result.horizon) setHorizon(result.horizon.kind === "long" ? result.horizon.label || result.horizon.value : result.horizon.value);
      });
    } else {
      onSelectInstrument(id);
      void load(requestedTarget, () => api.analyzeInstrument(id, Number(requestedHorizon) || 21));
    }
  }, [load, onSelectInstrument, onSelectModel]);

  useEffect(() => {
    if (!defaultTarget || isCurrentTarget(defaultTarget)) return;
    setTarget(defaultTarget);
    runAnalysis(defaultTarget, horizon);
  }, [defaultTarget, horizon, isCurrentTarget, runAnalysis]);

  const applyPreset = (label: string) => {
    setHorizon(label);
    runAnalysis(target || defaultTarget, label);
  };

  return (
    <div className="view-stack">
      <header className="view-head">
        <div><div className="section-label">Analysis</div><h1>Instrument and model detail</h1><p>Forecast cones, momentum oscillators, and weighted signal evidence in the React terminal.</p></div>
        <form className="toolbar" onSubmit={(event) => { event.preventDefault(); runAnalysis(target || defaultTarget, horizon); }}>
          <label>Target<TerminalSelect ariaLabel="Analysis target" value={target} onChange={setTarget} options={options} /></label>
          <label>Horizon (5–90d)
            <input
              type="number"
              min={5}
              max={90}
              value={typeof horizon === "number" ? horizon : 21}
              onChange={(event) => setHorizon(Math.max(5, Math.min(90, Number(event.target.value) || 21)))}
            />
          </label>
          <div className="horizon-presets" role="group" aria-label="Strategic projection horizon">
            {LONG_HORIZON_PRESETS.map((label) => (
              <button
                key={label}
                type="button"
                className={horizon === label ? "active" : ""}
                disabled={!targetIsModel || !availableLong.includes(label)}
                onClick={() => applyPreset(label)}
              >
                {label}
              </button>
            ))}
          </div>
          <button type="submit">Analyze</button>
        </form>
      </header>
      {error && <div className="notice danger" role="alert">{error}</div>}
      {isLoading ? (
        <div className="loading" role="status">Loading analysis for the selected target...</div>
      ) : !payload ? (
        <EmptyState title="Select a target" body="Choose an instrument or model to load analytics." />
      ) : (
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
  const tactical = tacticalForecast(payload);
  const isModel = Boolean(payload.mandate);
  return (
    <>
      <DataQualityBanner payload={quality} />
      <Panel title={`${payload.name}${eligible ? "" : " preview"}`} meta={payload.source ? <SourcePill source={payload.source} /> : horizonTag(payload)}>
        <div className={`signal-strip ${eligible ? "" : "signal-strip--preview"}`}>
          <strong className={`signal-action action-${actionClass}`}>{signalLabel}</strong>
          <p>{payload.signal.headline_rationale || payload.signal.rationale}</p>
          <span>{eligible ? `${fmtNumber(payload.signal.conviction_pct, 0)}% conviction${payload.signal.conviction_band ? ` (${payload.signal.conviction_band})` : ""}` : "Research locked"}</span>
        </div>
        {!eligible && <div className="warning-list"><span>{quality.required_action || "Replace demo or mixed inputs before treating this as research evidence."}</span></div>}
        {payload.signal.caveats?.length ? <div className="warning-list">{payload.signal.caveats.map((caveat) => <span key={caveat}>{caveat}</span>)}</div> : null}
      </Panel>
      <section className="dashboard-grid">
        <Panel title={`Price, Trend and Signal Markers${panelSuffix}`} className="span-2">
          <PriceTrendChart
            labels={payload.series.dates}
            close={payload.series.close}
            sma50={payload.series.sma50 || []}
            sma200={payload.series.sma200 || []}
            bbUpper={payload.series.bb_upper || []}
            bbLower={payload.series.bb_lower || []}
            markers={payload.series.markers || []}
            height={260}
          />
        </Panel>
        <Panel title={`Metrics${panelSuffix}`}>
          <MetricsGrid metrics={payload.metrics} mandate={payload.mandate} />
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title={forecastTitle(payload.forecast) + panelSuffix} className="span-2">
          <ForecastPanel payload={payload} />
        </Panel>
        <Panel title={`Momentum Oscillators${panelSuffix}`}>
          <div className="oscillator-stack">
            <div className="oscillator-stack__label">RSI (14) with 30/70 bands</div>
            <RsiChart labels={payload.series.dates} values={payload.series.rsi || []} height={130} />
            <div className="oscillator-stack__label">MACD (12/26/9)</div>
            <MacdChart labels={payload.series.dates} macd={payload.series.macd || []} signal={payload.series.macd_signal || []} histogram={payload.series.macd_hist || []} height={130} />
          </div>
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title={`Signal Component Breakdown${panelSuffix}`} className="span-2">
          <SignalBreakdown signal={payload.signal} forecast={tactical} />
        </Panel>
        {payload.sentiment ? (
          <Panel title={`News Sentiment${panelSuffix}`}>
            <SentimentList sentiment={payload.sentiment} />
          </Panel>
        ) : (
          <Panel title={`Mandate Fit${panelSuffix}`}>
            <MandateFit payload={payload} />
          </Panel>
        )}
      </section>
      <section className="dashboard-grid">
        <Panel title={`Backtest — Signal Strategy vs Buy and Hold${panelSuffix}`} className="span-2">
          <BacktestPanel payload={payload} eligible={eligible} />
        </Panel>
        <Panel title={`Drawdown${panelSuffix}`}>
          <DrawdownChart labels={payload.series.dates} values={drawdown} height={160} />
        </Panel>
      </section>
      <section className="dashboard-grid">
        <Panel title={`Daily Return Distribution${panelSuffix}`}>
          <HistogramChart values={dailyReturns} label="Daily return %" buckets={9} tone={eligible ? "info" : "warning"} />
        </Panel>
        {isModel && (
          <Panel title={`Model Insights${panelSuffix}`} className="span-2" meta={insightsMeta(payload.insights)}>
            <InsightsList insights={payload.insights} />
          </Panel>
        )}
      </section>
      {payload.holdings && (
        <Panel
          title={`Holdings${panelSuffix}`}
          meta={payload.concentration ? `HHI ${fmtNumber(payload.concentration.hhi, 2)} · ${fmtNumber(payload.concentration.n_eff, 1)} effective · avg corr ${fmtNumber(payload.concentration.corr_mean, 2)}` : undefined}
        >
          <div className="holdings-table">
            {payload.holdings.map((holding) => (
              <div key={String(holding.ticker)}>
                <strong>{String(holding.ticker)}</strong>
                <span>{fmtPct(Number(holding.weight) * 100)}</span>
                <span>{String(holding.source || "unavailable")}</span>
                <span>{String(holding.signal || "—")}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}

function horizonTag(payload: AnalysisResponse) {
  if (!payload.horizon) return payload.mandate?.label;
  const tag = payload.horizon.kind === "long"
    ? `${payload.horizon.label} strategic projection`
    : `${payload.horizon.value}d tactical signal`;
  return payload.mandate ? `${payload.mandate.label} · ${tag}` : tag;
}

function forecastTitle(forecast: AnalysisForecast): string {
  if (forecast.kind === "long") return `${forecast.label} Strategic Value Projection ($10,000 base)`;
  return "Return Forecast and Confidence Cone";
}

function tacticalForecast(payload: AnalysisResponse): TacticalForecast | undefined {
  if (payload.forecast_short) return payload.forecast_short;
  return payload.forecast.kind === "long" ? undefined : payload.forecast;
}

function MetricsGrid({ metrics, mandate }: { metrics: MetricSet; mandate?: AnalysisMandate }) {
  return (
    <div className="metric-grid">
      {Object.entries(metrics).slice(0, 8).map(([key, value]) => (
        <StatTile key={key} label={titleCase(key)} value={fmtAuto(value)} />
      ))}
      {mandate && typeof mandate.target_vol_pct === "number" && (
        <StatTile label="Mandate Vol Target" value={`≤ ${fmtNumber(mandate.target_vol_pct, 0)}%`} />
      )}
      {mandate && typeof mandate.max_drawdown_tolerance_pct === "number" && (
        <StatTile label="Drawdown Tolerance" value={`−${fmtNumber(mandate.max_drawdown_tolerance_pct, 0)}%`} />
      )}
    </div>
  );
}

function ForecastPanel({ payload }: { payload: AnalysisResponse }) {
  const forecast = payload.forecast;
  if (forecast.kind === "long") return <LongForecastPanel forecast={forecast} mandate={payload.mandate} />;
  return <TacticalForecastPanel forecast={forecast} series={payload.series} />;
}

function TacticalForecastPanel({ forecast, series }: { forecast: TacticalForecast; series: AnalysisResponse["series"] }) {
  const accuracy = forecast.quality?.directional_accuracy;
  const stats = [
    { label: "Expected return", value: fmtPct(forecast.expected_return_pct), tone: toneFor(forecast.expected_return_pct) },
    { label: "Annualized drift", value: fmtPct(forecast.annualized_drift_pct), tone: toneFor(forecast.annualized_drift_pct) },
    { label: "Expected vol", value: `${fmtNumber(forecast.expected_vol_pct, 1)}%`, tone: "neutral" },
    { label: "P(up)", value: `${fmtNumber(forecast.prob_up * 100, 0)}%`, tone: "neutral" },
    { label: "Dir. accuracy", value: accuracy == null ? "—" : `${fmtNumber(accuracy * 100, 1)}%`, tone: "neutral" },
  ];
  return (
    <div className="forecast-panel">
      <div className="metric-grid">
        {stats.map((stat) => <StatTile key={stat.label} label={stat.label} value={stat.value} tone={stat.tone} />)}
      </div>
      <ForecastConeChart points={tacticalConePoints(series, forecast)} ariaLabel={`${forecast.horizon_days} day forecast confidence cone`} />
      <p className="forecast-note">{forecast.horizon_days}d horizon · Monte-Carlo percentile cone (P05/P25/median/P75/P95) anchored to the last close.</p>
    </div>
  );
}

function LongForecastPanel({ forecast, mandate }: { forecast: LongForecast; mandate?: AnalysisMandate }) {
  const cagr = forecast.cagr_pct;
  const breachPct = forecast.prob_breach_maxdd * 100;
  const tolerance = mandate?.max_drawdown_tolerance_pct;
  const stats = [
    { label: "Median value", value: fmtMoney(forecast.terminal.p50), tone: "neutral" },
    { label: "Median CAGR", value: fmtPct(cagr.p50), tone: toneFor(cagr.p50) },
    { label: "P05–P95 CAGR", value: `${fmtNumber(cagr.p05, 1)}% … ${fmtNumber(cagr.p95, 1)}%`, tone: "neutral" },
    { label: "Prob. positive", value: `${fmtNumber(forecast.prob_positive * 100, 0)}%`, tone: "neutral" },
    { label: `Meets ${fmtNumber(forecast.mandate_target_pct, 1)}% target`, value: `${fmtNumber(forecast.prob_meets_mandate * 100, 0)}%`, tone: "neutral" },
    { label: "Median path drawdown", value: `${fmtNumber(forecast.drawdown_median_pct, 0)}%`, tone: "negative" },
    { label: "Value range", value: `${fmtMoney(forecast.terminal.p05)} – ${fmtMoney(forecast.terminal.p95)}`, tone: "neutral" },
  ];
  return (
    <div className="forecast-panel">
      <div className="metric-grid">
        {stats.map((stat) => <StatTile key={stat.label} label={stat.label} value={stat.value} tone={stat.tone} />)}
      </div>
      <div className={`notice ${breachPct > 20 ? "danger" : ""}`}>
        {fmtNumber(breachPct, 0)}% of simulated paths breach the mandate&apos;s {tolerance != null ? `−${fmtNumber(tolerance, 0)}%` : "maximum"} drawdown tolerance
        (worst-tail path drawdown {fmtNumber(forecast.drawdown_p95_pct, 0)}%).
      </div>
      <ForecastConeChart points={longConePoints(forecast)} baseline={forecast.base_value} ariaLabel={`${forecast.label} strategic value projection cone`} />
      <p className="forecast-note">
        drift {fmtPct(forecast.params.mu_long_pct)}/yr (λ{fmtNumber(forecast.params.anchor_weight_lambda, 2)} to anchor) · vol {fmtNumber(forecast.params.sigma_eff_pct, 1)}% · {forecast.disclaimer}
      </p>
    </div>
  );
}

function tacticalConePoints(series: AnalysisResponse["series"], forecast: TacticalForecast): ForecastConePoint[] {
  const tail = 60;
  const histDates = series.dates.slice(-tail);
  const histClose = series.close.slice(-tail);
  const anchor = [...histClose].reverse().find((value): value is number => typeof value === "number" && Number.isFinite(value)) ?? null;
  const history: ForecastConePoint[] = histDates.map((date, index) => {
    const isAnchor = index === histDates.length - 1;
    const band = isAnchor ? anchor : null;
    return { date, actual: histClose[index] ?? null, median: band, p05: band, p25: band, p75: band, p95: band };
  });
  const projection: ForecastConePoint[] = forecast.dates.map((date, index) => ({
    date,
    actual: null,
    median: forecast.bands.p50?.[index] ?? null,
    p05: forecast.bands.p05?.[index] ?? null,
    p25: forecast.bands.p25?.[index] ?? null,
    p75: forecast.bands.p75?.[index] ?? null,
    p95: forecast.bands.p95?.[index] ?? null,
  }));
  return [...history, ...projection];
}

function longConePoints(forecast: LongForecast): ForecastConePoint[] {
  const base = forecast.base_value;
  const start: ForecastConePoint = { date: "now", actual: null, median: base, p05: base, p25: base, p75: base, p95: base };
  const projection: ForecastConePoint[] = forecast.dates.map((date, index) => ({
    date,
    actual: null,
    median: forecast.bands.p50?.[index] ?? null,
    p05: forecast.bands.p05?.[index] ?? null,
    p25: forecast.bands.p25?.[index] ?? null,
    p75: forecast.bands.p75?.[index] ?? null,
    p95: forecast.bands.p95?.[index] ?? null,
  }));
  return [start, ...projection];
}

function SignalBreakdown({ signal, forecast }: { signal: AnalysisSignal; forecast?: TacticalForecast }) {
  const components = signal.components || [];
  if (!components.length) return <EmptyState title="No component evidence" body="Signal component breakdown appears once the composite signal is computed." />;
  const ordered = [...components].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
  const scalers = [
    typeof signal.vol_penalty === "number" ? `vol penalty ×${fmtNumber(signal.vol_penalty, 2)}` : null,
    typeof signal.mandate_fit === "number" && signal.mandate_fit !== 1 ? `mandate fit ×${fmtNumber(signal.mandate_fit, 2)}` : null,
  ].filter(Boolean);
  return (
    <div className="signal-breakdown">
      <div className="component-breakdown">
        {components.map((component) => {
          const width = Math.min(Math.abs(component.contribution) * 200, 50); // half-track %
          const positive = component.contribution >= 0;
          return (
            <div className="component-row" key={component.name}>
              <div className="component-row__name">
                {titleCase(component.name)}
                <span className="weight-chip">{fmtNumber(component.effective_weight * 100, 0)}%</span>
              </div>
              <div className="component-track">
                <i className="component-track__zero" />
                <i className={`component-track__bar ${positive ? "is-positive" : "is-negative"}`} style={{ width: `${width}%` }} />
              </div>
              <b className={positive ? "tone-positive" : "tone-negative"}>{fmtNumber(component.contribution, 2)}</b>
            </div>
          );
        })}
      </div>
      <div className="clause-list">
        {ordered.filter((component) => component.clause).map((component) => (
          <p key={component.name}>{component.clause}</p>
        ))}
      </div>
      {forecast?.feature_weights?.length ? (
        <div className="feat-chips" aria-label="Forecast feature weights">
          {forecast.feature_weights.slice(0, 6).map((feature) => (
            <span key={feature.feature} className="feat-chip">{feature.feature}: {feature.weight >= 0 ? "+" : ""}{fmtNumber(feature.weight, 3)}</span>
          ))}
        </div>
      ) : null}
      {scalers.length ? <p className="forecast-note">Composite scaled by {scalers.join(" · ")} before the {String(signal.action || "").toUpperCase()} call.</p> : null}
    </div>
  );
}

function SentimentList({ sentiment }: { sentiment: SentimentPayload }) {
  const aggregateTone = sentiment.aggregate_label === "positive" ? "positive" : sentiment.aggregate_label === "negative" ? "negative" : "neutral";
  return (
    <div className="sentiment-panel">
      <div className="sentiment-panel__aggregate">
        <span className={`sent-chip sent-${aggregateTone}`}>{sentiment.aggregate_label} {sentiment.aggregate_score >= 0 ? "+" : ""}{fmtNumber(sentiment.aggregate_score, 2)}</span>
        <small>{sentiment.count} scored headline{sentiment.count === 1 ? "" : "s"}</small>
      </div>
      {!sentiment.items.length ? (
        <p className="forecast-note">No headlines available for this instrument.</p>
      ) : (
        <ul className="sentiment-list">
          {sentiment.items.map((item) => (
            <li key={item.headline}>
              <span>{item.headline}</span>
              <span className={`sent-chip sent-${item.label === "positive" ? "positive" : item.label === "negative" ? "negative" : "neutral"}`}>
                {item.label} {item.score >= 0 ? "+" : ""}{fmtNumber(item.score, 2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MandateFit({ payload }: { payload: AnalysisResponse }) {
  const mandate = payload.mandate;
  const metrics = payload.metrics;
  const vol = Number(metrics.annual_vol_pct);
  const dd = Math.abs(Number(metrics.max_drawdown_pct));
  const annualReturn = Number(metrics.annual_return_pct);
  const targetVol = mandate?.target_vol_pct;
  const tolerance = mandate?.max_drawdown_tolerance_pct;
  const targetReturn = typeof mandate?.target_return_pct === "number"
    ? mandate.target_return_pct
    : payload.forecast.kind === "long" ? payload.forecast.mandate_target_pct : undefined;
  if (!mandate || typeof targetVol !== "number" || typeof tolerance !== "number") {
    return <EmptyState title="No mandate context" body="Mandate-fit bars appear for model analyses with a configured mandate." />;
  }
  const rows = [
    { label: "Volatility vs target", value: `${fmtNumber(vol, 1)}% / ${fmtNumber(targetVol, 0)}%`, pct: (vol / (targetVol * 2)) * 100, ok: vol <= targetVol * 1.15 },
    { label: "Max drawdown vs tolerance", value: `−${fmtNumber(dd, 1)}% / −${fmtNumber(tolerance, 0)}%`, pct: (dd / (tolerance * 2)) * 100, ok: dd <= tolerance },
    ...(typeof targetReturn === "number"
      ? [{
          label: "Return vs mandate target",
          value: `${fmtPct(annualReturn)} / ${fmtNumber(targetReturn, 1)}%`,
          pct: (Math.max(annualReturn, 0) / Math.max(targetReturn * 2, 1)) * 100,
          ok: annualReturn >= targetReturn,
        }]
      : []),
  ];
  return (
    <div className="mandate-fit">
      {rows.map((row) => (
        <div className="fit-item" key={row.label}>
          <div className="fit-item__head">
            <span>{row.label}</span>
            <b className={row.ok ? "tone-positive" : "tone-negative"}>{row.value}</b>
          </div>
          <div className="fit-bar"><i className={row.ok ? "is-positive" : "is-negative"} style={{ width: `${Math.min(row.pct, 100)}%` }} /></div>
        </div>
      ))}
      {typeof payload.signal.mandate_fit === "number" && (
        <p className="forecast-note">Signal conviction scaled ×{fmtNumber(payload.signal.mandate_fit, 2)} for mandate fit.</p>
      )}
    </div>
  );
}

function BacktestPanel({ payload, eligible }: { payload: AnalysisResponse; eligible: boolean }) {
  const backtest = payload.backtest;
  if (!backtest.strategy || !backtest.benchmark) {
    return <EmptyState title="Backtest unavailable" body={backtest.error || "Backtest appears when enough history is available."} />;
  }
  const stats = [
    { label: "Strategy return", value: fmtPct(backtest.strategy.total_return_pct), tone: toneFor(backtest.strategy.total_return_pct) },
    { label: "Buy and hold", value: fmtPct(backtest.benchmark.total_return_pct), tone: toneFor(backtest.benchmark.total_return_pct) },
    { label: "Strategy Sharpe", value: fmtNumber(backtest.strategy.sharpe, 2), tone: "neutral" },
    { label: "Strategy max DD", value: `${fmtNumber(backtest.strategy.max_drawdown_pct, 1)}%`, tone: "neutral" },
    { label: "Win rate", value: `${fmtNumber(backtest.win_rate_pct, 0)}%`, tone: "neutral" },
    { label: "Trades", value: String(backtest.n_trades ?? "—"), tone: "neutral" },
    { label: "Time in market", value: `${fmtNumber(backtest.exposure_pct, 0)}%`, tone: "neutral" },
    { label: "Vol reduction", value: `${fmtNumber(backtest.benchmark.annual_vol_pct - backtest.strategy.annual_vol_pct, 1)}%`, tone: "neutral" },
  ];
  return (
    <div className="forecast-panel">
      <div className="metric-grid">
        {stats.map((stat) => <StatTile key={stat.label} label={stat.label} value={stat.value} tone={eligible ? stat.tone : "neutral"} />)}
      </div>
      <EquityCurveChart labels={backtest.dates || []} strategy={backtest.strategy_curve || []} benchmark={backtest.benchmark_curve || []} height={190} />
    </div>
  );
}

function insightsMeta(insights?: ModelInsight[]) {
  if (!insights) return undefined;
  return insights.length ? `${insights.length} finding${insights.length > 1 ? "s" : ""}` : "no issues flagged";
}

function InsightsList({ insights }: { insights?: ModelInsight[] }) {
  if (!insights?.length) {
    return <p className="forecast-note">No mandate, concentration, or risk issues flagged for this model.</p>;
  }
  return (
    <div className="insights-list">
      {insights.map((insight) => (
        <div className={`insight-card severity-${insight.severity === "high" ? "high" : insight.severity === "medium" ? "medium" : "low"}`} key={insight.id + insight.message}>
          <div className="insight-card__head">
            <span className="insight-card__severity">{insight.severity}</span>
            <span className="insight-card__category">{titleCase(insight.category)}</span>
          </div>
          <p>{insight.message}</p>
          <small>{insight.suggested_action}</small>
        </div>
      ))}
    </div>
  );
}

function toneFor(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "neutral";
  return value >= 0 ? "positive" : "negative";
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

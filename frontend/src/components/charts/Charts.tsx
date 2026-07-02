import { lazy, Suspense } from "react";
import type { EChartsOption } from "echarts";
import { fmtNumber } from "../../utils/format";
import { EmptyChartState, LoadingChartState } from "./chartStates";
import { donutOption } from "./adapters/donut";
import { drawdownOption } from "./adapters/drawdown";
import { equityCurveOption } from "./adapters/equity";
import { forecastConeOption, type ForecastConePoint } from "./adapters/forecastCone";
import { histogramOption } from "./adapters/histogram";
import { macdOption } from "./adapters/macd";
import { multiLineOption } from "./adapters/multiLine";
import { priceTrendOption, type PriceTrendMarker } from "./adapters/priceTrend";
import { rollingSharpeOption } from "./adapters/rollingSharpe";
import { rsiOption } from "./adapters/rsi";
import { scoreScatterOption } from "./adapters/scoreScatter";

type ChartTone = "positive" | "negative" | "warning" | "info" | "neutral";
type ChartPoint = { label: string; x: number; y: number; size?: number; tone?: string; meta?: string };
type ChartSegment = { label: string; value: number; tone?: string };
type NullableNumber = number | null | undefined;

const HeliosEChart = lazy(() => import("./HeliosEChart").then((module) => ({ default: module.HeliosEChart })));

function safeTone(tone?: string): ChartTone {
  if (tone === "positive" || tone === "negative" || tone === "warning" || tone === "info" || tone === "neutral") return tone;
  return "neutral";
}

export function ScoreBar({ value, tone = "positive" }: { value?: number; tone?: string }) {
  const safe = Math.max(0, Math.min(100, Number(value) || 0));
  const toneClass = safeTone(tone);
  return (
    <div className="score-bar" aria-label={`Score ${fmtNumber(safe, 1)} out of 100`}>
      <span className={`score-bar__fill tone-${toneClass}`} style={{ width: `${safe}%` }} />
      <b>{fmtNumber(safe, 1)}</b>
    </div>
  );
}

export function MiniBars({ rows }: { rows: Array<{ label: string; value: number; tone?: string }> }) {
  if (!rows.length) return <div className="chart-empty">No chartable real-data rows.</div>;
  return (
    <div className="mini-bars">
      {rows.map((row) => {
        const safe = Math.max(0, Math.min(100, Number(row.value) || 0));
        const toneClass = safeTone(row.tone || "positive");
        return (
          <div className="mini-bars__row" key={row.label}>
            <span>{row.label}</span>
            <div><i className={`tone-${toneClass}`} style={{ width: `${safe}%` }} /></div>
            <b>{fmtNumber(row.value, 1)}</b>
          </div>
        );
      })}
    </div>
  );
}

export function ChartSummary({ items }: { items: Array<{ label: string; value: string; tone?: string }> }) {
  if (!items.length) return null;
  return (
    <div className="chart-summary" aria-label="Chart summary">
      {items.map((item) => (
        <span key={item.label} className={`tone-${safeTone(item.tone || "neutral")}`}>
          <b>{item.value}</b>
          <small>{item.label}</small>
        </span>
      ))}
    </div>
  );
}

export function HistogramChart({
  values,
  label = "Value",
  buckets = 8,
  tone = "info",
  min,
  max,
  height = 190,
}: {
  values: Array<number | null | undefined>;
  label?: string;
  buckets?: number;
  tone?: string;
  min?: number;
  max?: number;
  height?: number;
}) {
  const clean = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (clean.length < 2) {
    return <EmptyChartState body="Histogram appears when enough real-data points are available." minHeight={height} />;
  }
  return (
    <LazyHeliosEChart
      option={histogramOption(clean, { label, buckets, tone, min, max })}
      height={height}
      ariaLabel={`${label} distribution histogram`}
    />
  );
}

export function ScoreScatter({
  points,
  xLabel = "Risk score",
  yLabel = "Opportunity score",
  height = 220,
}: {
  points: ChartPoint[];
  xLabel?: string;
  yLabel?: string;
  height?: number;
}) {
  const clean = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!clean.length) {
    return <EmptyChartState body="Score map appears when real ranked candidates are available." minHeight={height} />;
  }
  return (
    <LazyHeliosEChart
      option={scoreScatterOption(clean, xLabel, yLabel)}
      height={height}
      ariaLabel={`${yLabel} by ${xLabel} scatter chart`}
    />
  );
}

export function DonutChart({
  segments,
  centerLabel = "Total",
  centerValue,
}: {
  segments: ChartSegment[];
  centerLabel?: string;
  centerValue?: string;
}) {
  const clean = segments.filter((segment) => Number.isFinite(segment.value) && segment.value > 0);
  if (!clean.length) return <div className="chart-empty">Donut chart appears when weighted rows are available.</div>;
  const total = clean.reduce((sum, segment) => sum + segment.value, 0) || 1;
  return (
    <div className="donut-chart">
      <div className="donut-chart__ring">
        <LazyHeliosEChart option={donutOption(clean)} height={156} ariaLabel={`${centerLabel} allocation donut chart`} />
        <span><b>{centerValue || fmtNumber(total, 1)}</b><small>{centerLabel}</small></span>
      </div>
      <div className="donut-chart__legend">
        {clean.slice(0, 8).map((segment) => (
          <span key={segment.label} className={`tone-${safeTone(segment.tone || "info")}`}>
            <i />
            <b>{segment.label}</b>
            <small>{fmtNumber(segment.value, 1)} · {fmtNumber((segment.value / total) * 100, 0)}%</small>
          </span>
        ))}
      </div>
    </div>
  );
}

export function EquityCurveChart({
  labels,
  strategy,
  benchmark,
  height = 220,
}: {
  labels: string[];
  strategy: NullableNumber[];
  benchmark?: NullableNumber[];
  height?: number;
}) {
  const points = labels.map((date, index) => ({
    date,
    strategy: chartNumber(strategy[index]),
    benchmark: chartNumber(benchmark?.[index]),
  }));
  if (!hasEnoughPoints(points.map((point) => point.strategy))) {
    return <EmptyChartState body="Equity curve appears when enough strategy history is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={equityCurveOption(points)} height={height} ariaLabel="Equity curve versus benchmark" />;
}

export function DrawdownChart({
  labels,
  values,
  height = 180,
}: {
  labels: string[];
  values: NullableNumber[];
  height?: number;
}) {
  const points = labels.map((date, index) => ({ date, drawdown: chartNumber(values[index]) }));
  if (!hasEnoughPoints(points.map((point) => point.drawdown))) {
    return <EmptyChartState body="Drawdown chart appears when enough real-data history is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={drawdownOption(points)} height={height} ariaLabel="Drawdown chart" />;
}

export function RollingSharpeChart({
  labels,
  values,
  height = 180,
}: {
  labels: string[];
  values: NullableNumber[];
  height?: number;
}) {
  const points = labels.map((date, index) => {
    const value = chartNumber(values[index]);
    // Guard against degenerate windows (near-zero volatility) whose ratios
    // explode by orders of magnitude and destroy the axis scale.
    return { date, sharpe: value !== null && Math.abs(value) <= 10 ? value : null };
  });
  if (!hasEnoughPoints(points.map((point) => point.sharpe))) {
    return <EmptyChartState body="Rolling Sharpe appears after enough strategy windows are available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={rollingSharpeOption(points)} height={height} ariaLabel="Rolling Sharpe chart" />;
}

export function PriceTrendChart({
  labels,
  close,
  sma50,
  sma200,
  bbUpper,
  bbLower,
  markers,
  height = 220,
}: {
  labels: string[];
  close: NullableNumber[];
  sma50?: NullableNumber[];
  sma200?: NullableNumber[];
  bbUpper?: NullableNumber[];
  bbLower?: NullableNumber[];
  markers?: PriceTrendMarker[];
  height?: number;
}) {
  const points = labels.map((date, index) => ({
    date,
    close: chartNumber(close[index]),
    sma50: chartNumber(sma50?.[index]),
    sma200: chartNumber(sma200?.[index]),
    bbUpper: chartNumber(bbUpper?.[index]),
    bbLower: chartNumber(bbLower?.[index]),
  }));
  if (!hasEnoughPoints(points.map((point) => point.close))) {
    return <EmptyChartState body="Price trend appears when enough price history is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={priceTrendOption(points, markers ?? [])} height={height} ariaLabel="Price, moving-average, and Bollinger trend chart with signal markers" />;
}

export function ForecastConeChart({
  points,
  baseline,
  height = 220,
  ariaLabel = "Forecast confidence cone chart",
}: {
  points: ForecastConePoint[];
  baseline?: number;
  height?: number;
  ariaLabel?: string;
}) {
  const plottable = points.filter((point) => [point.median, point.actual].some((value) => typeof value === "number" && Number.isFinite(value)));
  if (plottable.length < 2) {
    return <EmptyChartState body="Forecast cone appears when a forecast is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={forecastConeOption(points, baseline)} height={height} ariaLabel={ariaLabel} />;
}

export function RsiChart({
  labels,
  values,
  height = 150,
}: {
  labels: string[];
  values: NullableNumber[];
  height?: number;
}) {
  const points = labels.map((date, index) => ({ date, rsi: chartNumber(values[index]) }));
  if (!hasEnoughPoints(points.map((point) => point.rsi))) {
    return <EmptyChartState body="RSI appears when enough real-data history is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={rsiOption(points)} height={height} ariaLabel="RSI oscillator chart with 30/70 bands" />;
}

export function MacdChart({
  labels,
  macd,
  signal,
  histogram,
  height = 150,
}: {
  labels: string[];
  macd: NullableNumber[];
  signal?: NullableNumber[];
  histogram?: NullableNumber[];
  height?: number;
}) {
  const points = labels.map((date, index) => ({
    date,
    macd: chartNumber(macd[index]),
    signal: chartNumber(signal?.[index]),
    histogram: chartNumber(histogram?.[index]),
  }));
  if (!hasEnoughPoints(points.map((point) => point.macd))) {
    return <EmptyChartState body="MACD appears when enough real-data history is available." minHeight={height} />;
  }
  return <LazyHeliosEChart option={macdOption(points)} height={height} ariaLabel="MACD line, signal, and histogram chart" />;
}

export function LineChart({
  labels,
  series,
  height = 180,
}: {
  labels: string[];
  series: Array<{ label: string; values: Array<number | null | undefined>; tone?: string }>;
  height?: number;
}) {
  const points = series.flatMap((item) => item.values.filter((value): value is number => typeof value === "number" && Number.isFinite(value)));
  if (labels.length < 2 || points.length < 2) {
    return <EmptyChartState body="Chart appears when enough history is available." minHeight={height} />;
  }
  return (
    <LazyHeliosEChart
      option={multiLineOption(labels, series)}
      height={height}
      ariaLabel={`Historical line chart: ${series.map((item) => item.label).join(", ")}`}
    />
  );
}

function chartNumber(value: NullableNumber): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function hasEnoughPoints(values: Array<number | null | undefined>): boolean {
  return values.filter((value): value is number => typeof value === "number" && Number.isFinite(value)).length >= 2;
}

function LazyHeliosEChart({
  option,
  height,
  ariaLabel,
}: {
  option: EChartsOption;
  height: number;
  ariaLabel: string;
}) {
  return (
    <Suspense fallback={<LoadingChartState minHeight={height} />}>
      <HeliosEChart option={option} height={height} ariaLabel={ariaLabel} />
    </Suspense>
  );
}

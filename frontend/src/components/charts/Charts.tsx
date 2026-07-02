import { lazy, Suspense } from "react";
import type { EChartsOption } from "echarts";
import { fmtNumber } from "../../utils/format";
import { EmptyChartState, LoadingChartState } from "./chartStates";
import { drawdownOption } from "./adapters/drawdown";
import { equityCurveOption } from "./adapters/equity";
import { forecastConeOption, type ForecastConePoint } from "./adapters/forecastCone";
import { macdOption } from "./adapters/macd";
import { priceTrendOption, type PriceTrendMarker } from "./adapters/priceTrend";
import { rollingSharpeOption } from "./adapters/rollingSharpe";
import { rsiOption } from "./adapters/rsi";

type ChartTone = "positive" | "negative" | "warning" | "info" | "neutral";
type ChartPoint = { label: string; x: number; y: number; size?: number; tone?: string; meta?: string };
type ChartSegment = { label: string; value: number; tone?: string };
type NullableNumber = number | null | undefined;

const HeliosEChart = lazy(() => import("./HeliosEChart").then((module) => ({ default: module.HeliosEChart })));

const TONE_COLORS: Record<ChartTone, string> = {
  positive: "#47d66f",
  negative: "#ff5c67",
  warning: "#f4c542",
  info: "#4c9dff",
  neutral: "#9aa8ba",
};

function safeTone(tone?: string): ChartTone {
  if (tone === "positive" || tone === "negative" || tone === "warning" || tone === "info" || tone === "neutral") return tone;
  return "neutral";
}

function finiteNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp(value: number, min = 0, max = 100): number {
  return Math.max(min, Math.min(max, value));
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
}: {
  values: Array<number | null | undefined>;
  label?: string;
  buckets?: number;
  tone?: string;
  min?: number;
  max?: number;
}) {
  const clean = values.filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (clean.length < 2) return <div className="chart-empty">Histogram appears when enough real-data points are available.</div>;
  const bucketCount = Math.max(3, Math.min(12, Math.round(buckets)));
  const floor = typeof min === "number" ? min : Math.min(...clean);
  const ceiling = typeof max === "number" ? max : Math.max(...clean);
  const span = ceiling - floor || 1;
  const counts = Array.from({ length: bucketCount }, () => 0);
  clean.forEach((value) => {
    const rawIndex = Math.floor(((value - floor) / span) * bucketCount);
    counts[clamp(rawIndex, 0, bucketCount - 1)] += 1;
  });
  const high = Math.max(...counts, 1);
  const toneClass = safeTone(tone);
  return (
    <div className="histogram-chart" role="img" aria-label={`${label} distribution histogram`}>
      <div className="histogram-chart__bars" style={{ gridTemplateColumns: `repeat(${bucketCount}, minmax(0, 1fr))` }}>
        {counts.map((count, index) => {
          const bucketStart = floor + (span / bucketCount) * index;
          const bucketEnd = floor + (span / bucketCount) * (index + 1);
          return (
            <span
              key={`${bucketStart}-${bucketEnd}`}
              className={`tone-${toneClass}`}
              style={{ height: `${Math.max(4, (count / high) * 100)}%` }}
              title={`${fmtNumber(bucketStart, 1)} to ${fmtNumber(bucketEnd, 1)}: ${count}`}
            >
              <b>{count}</b>
            </span>
          );
        })}
      </div>
      <div className="histogram-chart__axis">
        <span>{fmtNumber(floor, 1)}</span>
        <b>{label}</b>
        <span>{fmtNumber(ceiling, 1)}</span>
      </div>
    </div>
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
  if (!clean.length) return <div className="chart-empty">Score map appears when real ranked candidates are available.</div>;
  const width = 520;
  const pad = 34;
  const toX = (value: number) => pad + (clamp(value) / 100) * (width - pad * 2);
  const toY = (value: number) => pad + (1 - clamp(value) / 100) * (height - pad * 2);
  return (
    <div className="score-scatter">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${yLabel} by ${xLabel}`} style={{ height }}>
        <g className="grid-lines">
          {[25, 50, 75].map((value) => (
            <g key={value}>
              <line x1={toX(value)} x2={toX(value)} y1={pad} y2={height - pad} />
              <line x1={pad} x2={width - pad} y1={toY(value)} y2={toY(value)} />
            </g>
          ))}
        </g>
        <line className="chart-axis-line" x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} />
        <line className="chart-axis-line" x1={pad} x2={pad} y1={pad} y2={height - pad} />
        <text className="chart-axis-label" x={width / 2} y={height - 8}>{xLabel}</text>
        <text className="chart-axis-label" x={12} y={height / 2} transform={`rotate(-90 12 ${height / 2})`}>{yLabel}</text>
        {clean.map((point) => {
          const toneClass = safeTone(point.tone || (point.y >= 70 ? "positive" : point.y >= 50 ? "warning" : "neutral"));
          const radius = clamp(finiteNumber(point.size, 7), 4, 11);
          return (
            <g key={`${point.label}-${point.x}-${point.y}`}>
              <circle
                className={`score-scatter__dot tone-${toneClass}`}
                cx={toX(point.x)}
                cy={toY(point.y)}
                r={radius}
              >
                <title>{`${point.label}: ${yLabel} ${fmtNumber(point.y, 1)}, ${xLabel} ${fmtNumber(point.x, 1)}${point.meta ? `, ${point.meta}` : ""}`}</title>
              </circle>
              {radius >= 8 && <text className="score-scatter__label" x={toX(point.x) + radius + 3} y={toY(point.y) + 3}>{point.label}</text>}
            </g>
          );
        })}
      </svg>
    </div>
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
  let cursor = 0;
  const gradient = clean.map((segment) => {
    const start = cursor;
    cursor += (segment.value / total) * 100;
    const color = TONE_COLORS[safeTone(segment.tone || "info")];
    return `${color} ${start.toFixed(2)}% ${cursor.toFixed(2)}%`;
  }).join(", ");
  return (
    <div className="donut-chart">
      <div className="donut-chart__ring" style={{ background: `conic-gradient(${gradient})` }}>
        <span><b>{centerValue || fmtNumber(total, 1)}</b><small>{centerLabel}</small></span>
      </div>
      <div className="donut-chart__legend">
        {clean.slice(0, 8).map((segment) => (
          <span key={segment.label} className={`tone-${safeTone(segment.tone || "info")}`}>
            <i />
            <b>{segment.label}</b>
            <small>{fmtNumber(segment.value, 1)}</small>
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
  const points = labels.map((date, index) => ({ date, sharpe: chartNumber(values[index]) }));
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
  if (labels.length < 2 || points.length < 2) return <div className="chart-empty">Chart appears when enough history is available.</div>;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const width = 640;
  const pad = 12;
  const toPoint = (value: number, index: number) => {
    const x = pad + (index / Math.max(1, labels.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (value - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const segmentsFor = (values: Array<number | null | undefined>) => {
    const segments: string[][] = [];
    let current: string[] = [];
    labels.forEach((_, index) => {
      const value = values[index];
      if (typeof value === "number" && Number.isFinite(value)) {
        current.push(toPoint(value, index));
        return;
      }
      if (current.length > 1) segments.push(current);
      current = [];
    });
    if (current.length > 1) segments.push(current);
    return segments;
  };
  return (
    <div className="line-chart">
      <div className="line-chart__scale" aria-hidden="true">
        <span>{fmtNumber(max, 2)}</span>
        <span>{fmtNumber(min, 2)}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Historical line chart" style={{ height }}>
        <g className="grid-lines">
          {[0.25, 0.5, 0.75].map((pct) => <line key={pct} x1="0" x2={width} y1={height * pct} y2={height * pct} />)}
        </g>
        <line className="chart-axis-line" x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} />
        {series.flatMap((item) => {
          const toneClass = safeTone(item.tone || "info");
          return segmentsFor(item.values).map((segment, index) => (
            <polyline key={`${item.label}-${index}`} className={`line-chart__line tone-${toneClass}`} points={segment.join(" ")} />
          ));
        })}
      </svg>
      <div className="chart-legend">
        {series.map((item) => <span key={item.label} className={`tone-${safeTone(item.tone || "info")}`}>{item.label}</span>)}
      </div>
    </div>
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

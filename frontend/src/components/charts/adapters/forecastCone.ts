import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartGlow, chartLegend, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS, HELIOS_CHART_GRID_WITH_LEGEND, toneColor } from "../chartTheme";
import { lineOption } from "./equity";

export type ForecastConePoint = {
  date: string;
  actual?: number | null;
  median: number | null;
  p05?: number | null;
  p25?: number | null;
  p75?: number | null;
  p95?: number | null;
};

/** Invisible lower line + width series that stack into a percentile band fill. */
function bandPair(
  name: string,
  stack: string,
  lows: Array<number | null>,
  highs: Array<number | null>,
  fill: string,
): SeriesOption[] {
  const widths = lows.map((low, index) => {
    const high = highs[index];
    if (typeof low !== "number" || typeof high !== "number") return null;
    return high - low;
  });
  return [
    {
      name: `${name} base`,
      type: "line",
      data: lows,
      showSymbol: false,
      stack,
      lineStyle: { opacity: 0 },
      tooltip: { show: false },
      emphasis: { disabled: true },
    },
    {
      name,
      type: "line",
      data: widths,
      showSymbol: false,
      stack,
      lineStyle: { opacity: 0 },
      areaStyle: { color: fill },
      tooltip: { show: false },
      emphasis: { disabled: true },
    },
  ];
}

export function forecastConeOption(points: ForecastConePoint[], baseline?: number): EChartsOption {
  const dates = points.map((point) => point.date);
  const pick = (key: keyof ForecastConePoint) => points.map((point) => {
    const value = point[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  });
  const series: SeriesOption[] = [
    ...bandPair("P05–P95 band", "outer-band", pick("p05"), pick("p95"), chartAlpha("info", 0.1)),
    ...bandPair("P25–P75 band", "inner-band", pick("p25"), pick("p75"), chartAlpha("info", 0.18)),
    {
      name: "Median path",
      type: "line",
      data: pick("median"),
      showSymbol: false,
      lineStyle: { width: 2, color: toneColor("info"), type: "dashed" },
      itemStyle: { color: toneColor("info") },
      emphasis: { focus: "series" },
    },
    {
      name: "Actual",
      type: "line",
      data: pick("actual"),
      showSymbol: false,
      lineStyle: { width: 1.8, color: HELIOS_CHART_COLORS.text, ...chartGlow("ink", 0.28) },
      itemStyle: { color: HELIOS_CHART_COLORS.text },
      emphasis: { focus: "series" },
    },
  ];
  if (typeof baseline === "number" && Number.isFinite(baseline)) {
    series.push({
      name: "Start",
      type: "line",
      data: dates.map(() => baseline),
      showSymbol: false,
      lineStyle: { width: 1, color: HELIOS_CHART_COLORS.neutral, type: "dashed" },
      itemStyle: { color: HELIOS_CHART_COLORS.neutral },
      tooltip: { show: false },
      emphasis: { disabled: true },
    });
  }
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.price),
    grid: HELIOS_CHART_GRID_WITH_LEGEND,
    legend: chartLegend({
      data: ["Actual", "Median path", "P25–P75 band", "P05–P95 band"],
    }),
  };
}

import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartLegend, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";
import { lineOption } from "./equity";

export type ForecastConePoint = {
  date: string;
  expected: number | null;
  low?: number | null;
  high?: number | null;
};

export function forecastConeOption(points: ForecastConePoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const lows = points.map((point) => point.low ?? null);
  const bands = points.map((point) => {
    if (typeof point.low !== "number" || typeof point.high !== "number") return null;
    return point.high - point.low;
  });
  const series: SeriesOption[] = [
    {
      name: "Lower bound",
      type: "line",
      data: lows,
      showSymbol: false,
      stack: "confidence-band",
      lineStyle: { opacity: 0 },
      tooltip: { show: false },
      emphasis: { disabled: true },
    },
    {
      name: "Confidence band",
      type: "line",
      data: bands,
      showSymbol: false,
      stack: "confidence-band",
      lineStyle: { opacity: 0 },
      areaStyle: { color: chartAlpha("info", 0.15) },
      tooltip: { show: false },
      emphasis: { focus: "series" },
    },
    {
      name: "Expected",
      type: "line",
      data: points.map((point) => point.expected),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2.2, color: toneColor("info") },
      itemStyle: { color: toneColor("info") },
      emphasis: { focus: "series" },
    },
  ];
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.price),
    legend: chartLegend({
      data: ["Expected", "Confidence band"],
    }),
  };
}

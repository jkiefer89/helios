import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartLegend, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";
import { lineOption } from "./equity";

export type ForecastConePoint = {
  date: string;
  expected: number | null;
  low?: number | null;
  high?: number | null;
  innerLow?: number | null;
  innerHigh?: number | null;
  history?: number | null;
};

export type ForecastConeOptions = {
  baseline?: number;
};

function bandValue(low?: number | null, high?: number | null): number | null {
  if (typeof low !== "number" || typeof high !== "number") return null;
  return high - low;
}

export function forecastConeOption(points: ForecastConePoint[], options: ForecastConeOptions = {}): EChartsOption {
  const dates = points.map((point) => point.date);
  const hasInnerBand = points.some(
    (point) => typeof point.innerLow === "number" && typeof point.innerHigh === "number",
  );
  const hasHistory = points.some((point) => typeof point.history === "number");
  const series: SeriesOption[] = [
    {
      name: "Lower bound",
      type: "line",
      data: points.map((point) => point.low ?? null),
      showSymbol: false,
      stack: "confidence-band",
      lineStyle: { opacity: 0 },
      tooltip: { show: false },
      emphasis: { disabled: true },
    },
    {
      name: "Confidence band",
      type: "line",
      data: points.map((point) => bandValue(point.low, point.high)),
      showSymbol: false,
      stack: "confidence-band",
      lineStyle: { opacity: 0 },
      areaStyle: { color: chartAlpha("info", 0.12) },
      tooltip: { show: false },
      emphasis: { focus: "series" },
    },
  ];
  if (hasInnerBand) {
    series.push(
      {
        name: "Central lower bound",
        type: "line",
        data: points.map((point) => point.innerLow ?? null),
        showSymbol: false,
        stack: "central-band",
        lineStyle: { opacity: 0 },
        tooltip: { show: false },
        emphasis: { disabled: true },
      },
      {
        name: "Central band",
        type: "line",
        data: points.map((point) => bandValue(point.innerLow, point.innerHigh)),
        showSymbol: false,
        stack: "central-band",
        lineStyle: { opacity: 0 },
        areaStyle: { color: chartAlpha("info", 0.18) },
        tooltip: { show: false },
        emphasis: { focus: "series" },
      },
    );
  }
  series.push({
    name: "Expected",
    type: "line",
    data: points.map((point) => point.expected),
    showSymbol: false,
    smooth: true,
    lineStyle: { width: 2.2, color: toneColor("info") },
    itemStyle: { color: toneColor("info") },
    emphasis: { focus: "series" },
    ...(typeof options.baseline === "number"
      ? {
          markLine: {
            silent: true,
            symbol: "none",
            label: { show: false },
            lineStyle: { type: "dashed", color: HELIOS_CHART_COLORS.neutral, opacity: 0.6 },
            data: [{ yAxis: options.baseline }],
          },
        }
      : {}),
  });
  if (hasHistory) {
    series.push({
      name: "Actual",
      type: "line",
      data: points.map((point) => point.history ?? null),
      showSymbol: false,
      lineStyle: { width: 1.8, color: HELIOS_CHART_COLORS.text },
      itemStyle: { color: HELIOS_CHART_COLORS.text },
      emphasis: { focus: "series" },
    });
  }
  const legendEntries = [
    ...(hasHistory ? ["Actual"] : []),
    "Expected",
    ...(hasInnerBand ? ["Central band"] : []),
    "Confidence band",
  ];
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.price),
    legend: chartLegend({
      data: legendEntries,
    }),
  };
}

import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartTooltip, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export type MacdPoint = {
  date: string;
  macd: number | null;
  signal: number | null;
  histogram: number | null;
};

export function macdOption(points: MacdPoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const series: SeriesOption[] = [
    {
      name: "Histogram",
      type: "bar",
      data: points.map((point) => point.histogram),
      barMaxWidth: 5,
      itemStyle: {
        color: (params: { value?: unknown }) =>
          typeof params.value === "number" && params.value < 0 ? chartAlpha("negative", 0.5) : chartAlpha("positive", 0.5),
      },
      emphasis: { disabled: true },
    },
    {
      name: "MACD",
      type: "line",
      data: points.map((point) => point.macd),
      showSymbol: false,
      lineStyle: { width: 1.5, color: toneColor("info") },
      itemStyle: { color: toneColor("info") },
      emphasis: { focus: "series" },
    },
    {
      name: "Signal",
      type: "line",
      data: points.map((point) => point.signal),
      showSymbol: false,
      lineStyle: { width: 1.5, color: toneColor("warning") },
      itemStyle: { color: toneColor("warning") },
      emphasis: { focus: "series" },
    },
  ];
  return {
    tooltip: chartTooltip(HELIOS_CHART_FORMATTERS.ratio),
    legend: { show: false },
    xAxis: {
      type: "category",
      data: dates,
      axisTick: { show: false },
      axisLine: { lineStyle: { color: HELIOS_CHART_COLORS.axis } },
      axisLabel: {
        color: HELIOS_CHART_COLORS.muted,
        hideOverlap: true,
        margin: 12,
        formatter: (value: string | number) => HELIOS_CHART_FORMATTERS.date(String(value)),
      },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: HELIOS_CHART_COLORS.muted, formatter: HELIOS_CHART_FORMATTERS.ratio },
      splitLine: { lineStyle: { color: HELIOS_CHART_COLORS.grid } },
    },
    series,
  };
}

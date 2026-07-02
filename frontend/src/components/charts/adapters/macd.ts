import type { EChartsOption, SeriesOption } from "echarts";
import {
  chartAlpha,
  chartCategoryAxis,
  chartGlow,
  chartGuides,
  chartTooltip,
  chartValueAxis,
  HELIOS_CHART_FORMATTERS,
  toneColor,
} from "../chartTheme";

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
        borderRadius: 1,
        color: (params: { value?: unknown }) =>
          typeof params.value === "number" && params.value < 0 ? chartAlpha("negative", 0.5) : chartAlpha("positive", 0.5),
      },
      markLine: chartGuides([{ value: 0, tone: "neutral" }]),
      emphasis: { disabled: true },
    },
    {
      name: "MACD",
      type: "line",
      data: points.map((point) => point.macd),
      showSymbol: false,
      lineStyle: { width: 1.5, color: toneColor("info"), ...chartGlow("info", 0.3) },
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
    xAxis: chartCategoryAxis(dates, { boundaryGap: true }),
    yAxis: chartValueAxis(HELIOS_CHART_FORMATTERS.ratio),
    series,
  };
}

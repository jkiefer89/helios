import type { EChartsOption, SeriesOption } from "echarts";
import {
  chartAreaGradient,
  chartCategoryAxis,
  chartGlow,
  chartLegend,
  chartTooltip,
  chartValueAxis,
  HELIOS_CHART_COLORS,
  HELIOS_CHART_FORMATTERS,
  HELIOS_CHART_GRID_WITH_LEGEND,
  toneColor,
} from "../chartTheme";

export type EquityPoint = {
  date: string;
  strategy: number | null;
  benchmark?: number | null;
};

export function equityCurveOption(points: EquityPoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const series: SeriesOption[] = [
    {
      name: "Signal strategy",
      type: "line",
      data: points.map((point) => point.strategy),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2.25, color: toneColor("positive"), ...chartGlow("positive") },
      itemStyle: { color: toneColor("positive") },
      areaStyle: { color: chartAreaGradient("positive", 0.24) },
      emphasis: { focus: "series" },
    },
    {
      name: "Buy-and-hold",
      type: "line",
      data: points.map((point) => point.benchmark ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.6, color: HELIOS_CHART_COLORS.neutral, type: "dashed" },
      itemStyle: { color: HELIOS_CHART_COLORS.neutral },
      emphasis: { focus: "series" },
    },
  ];
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.ratio),
    grid: HELIOS_CHART_GRID_WITH_LEGEND,
    legend: chartLegend({ data: ["Signal strategy", "Buy-and-hold"] }),
  };
}

export function lineOption(
  dates: string[],
  series: SeriesOption[],
  yFormatter = HELIOS_CHART_FORMATTERS.number,
): EChartsOption {
  return {
    tooltip: chartTooltip(yFormatter),
    xAxis: chartCategoryAxis(dates),
    yAxis: chartValueAxis(yFormatter),
    series,
  };
}

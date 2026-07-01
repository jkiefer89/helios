import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartTooltip, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

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
      lineStyle: { width: 2.25, color: toneColor("positive") },
      areaStyle: { color: chartAlpha("positive", 0.13) },
      emphasis: { focus: "series" },
    },
    {
      name: "Buy-and-hold",
      type: "line",
      data: points.map((point) => point.benchmark ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.8, color: HELIOS_CHART_COLORS.neutral, type: "dashed" },
      emphasis: { focus: "series" },
    },
  ];
  return lineOption(dates, series, HELIOS_CHART_FORMATTERS.ratio);
}

export function lineOption(
  dates: string[],
  series: SeriesOption[],
  yFormatter = HELIOS_CHART_FORMATTERS.number,
): EChartsOption {
  return {
    tooltip: chartTooltip(yFormatter),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
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
      axisLabel: { color: HELIOS_CHART_COLORS.muted, formatter: yFormatter },
      splitLine: { lineStyle: { color: HELIOS_CHART_COLORS.grid } },
    },
    series,
  };
}

import type { EChartsOption, SeriesOption } from "echarts";
import { chartAlpha, chartTooltip, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS } from "../chartTheme";

export type RsiPoint = {
  date: string;
  rsi: number | null;
};

const RSI_LINE_COLOR = "#a78bfa"; // matches the terminal --violet accent

export function rsiOption(points: RsiPoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const guide = (name: string, level: number, tone: "negative" | "positive"): SeriesOption => ({
    name,
    type: "line",
    data: dates.map(() => level),
    showSymbol: false,
    lineStyle: { width: 1, color: chartAlpha(tone, 0.45), type: "dashed" },
    tooltip: { show: false },
    emphasis: { disabled: true },
  });
  const series: SeriesOption[] = [
    guide("Overbought 70", 70, "negative"),
    guide("Oversold 30", 30, "positive"),
    {
      name: "RSI",
      type: "line",
      data: points.map((point) => point.rsi),
      showSymbol: false,
      lineStyle: { width: 1.6, color: RSI_LINE_COLOR },
      itemStyle: { color: RSI_LINE_COLOR },
      emphasis: { focus: "series" },
    },
  ];
  return {
    tooltip: chartTooltip(HELIOS_CHART_FORMATTERS.number),
    legend: { show: false },
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
      min: 0,
      max: 100,
      axisLabel: { color: HELIOS_CHART_COLORS.muted, formatter: HELIOS_CHART_FORMATTERS.number },
      splitLine: { lineStyle: { color: HELIOS_CHART_COLORS.grid } },
    },
    series,
  };
}

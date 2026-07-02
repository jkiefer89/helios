import type { EChartsOption, SeriesOption } from "echarts";
import {
  chartCategoryAxis,
  chartGlow,
  chartGuides,
  chartTooltip,
  chartValueAxis,
  HELIOS_CHART_COLORS,
  HELIOS_CHART_FORMATTERS,
} from "../chartTheme";

export type RsiPoint = {
  date: string;
  rsi: number | null;
};

// Matches the terminal --violet accent ("accent" paint in chartTheme).
const RSI_LINE_COLOR = HELIOS_CHART_COLORS.accent;

export function rsiOption(points: RsiPoint[]): EChartsOption {
  const dates = points.map((point) => point.date);
  const series: SeriesOption[] = [
    {
      name: "RSI",
      type: "line",
      data: points.map((point) => point.rsi),
      showSymbol: false,
      lineStyle: { width: 1.6, color: RSI_LINE_COLOR, ...chartGlow("accent", 0.32) },
      itemStyle: { color: RSI_LINE_COLOR },
      markLine: chartGuides([
        { value: 70, tone: "negative", label: "70" },
        { value: 30, tone: "positive", label: "30" },
      ]),
      emphasis: { focus: "series" },
    },
  ];
  return {
    tooltip: chartTooltip(HELIOS_CHART_FORMATTERS.number),
    xAxis: chartCategoryAxis(dates),
    yAxis: chartValueAxis(HELIOS_CHART_FORMATTERS.number, { scale: false, min: 0, max: 100 }),
    series,
  };
}

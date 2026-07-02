import type { EChartsOption } from "echarts";
import { lineOption } from "./equity";
import { chartAreaGradient, chartGlow, chartGuides, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export function rollingSharpeOption(points: Array<{ date: string; sharpe: number | null }>): EChartsOption {
  return lineOption(
    points.map((point) => point.date),
    [{
      name: "Rolling Sharpe",
      type: "line",
      data: points.map((point) => point.sharpe),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2, color: toneColor("warning"), ...chartGlow("warning", 0.32) },
      itemStyle: { color: toneColor("warning") },
      areaStyle: { color: chartAreaGradient("warning", 0.18) },
      markLine: chartGuides([
        { value: 0, tone: "neutral" },
        { value: 1, tone: "positive", label: "1.0" },
      ]),
      emphasis: { focus: "series" },
    }],
    HELIOS_CHART_FORMATTERS.ratio,
  );
}

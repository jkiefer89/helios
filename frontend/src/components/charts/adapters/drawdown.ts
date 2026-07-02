import type { EChartsOption } from "echarts";
import { lineOption } from "./equity";
import { chartAreaGradient, chartGlow, chartGuides, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export function drawdownOption(points: Array<{ date: string; drawdown: number | null }>): EChartsOption {
  return lineOption(
    points.map((point) => point.date),
    [{
      name: "Drawdown",
      type: "line",
      data: points.map((point) => point.drawdown),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2, color: toneColor("negative"), ...chartGlow("negative", 0.32) },
      itemStyle: { color: toneColor("negative") },
      // Deepest at the trough, fading up toward the zero waterline.
      areaStyle: { color: chartAreaGradient("negative", 0.04, 0.26) },
      markLine: chartGuides([{ value: 0, tone: "neutral" }]),
      emphasis: { focus: "series" },
    }],
    HELIOS_CHART_FORMATTERS.percent,
  );
}

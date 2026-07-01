import type { EChartsOption } from "echarts";
import { lineOption } from "./equity";
import { chartAlpha, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export function drawdownOption(points: Array<{ date: string; drawdown: number | null }>): EChartsOption {
  return lineOption(
    points.map((point) => point.date),
    [{
      name: "Drawdown",
      type: "line",
      data: points.map((point) => point.drawdown),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2, color: toneColor("negative") },
      areaStyle: { color: chartAlpha("negative", 0.14) },
      emphasis: { focus: "series" },
    }],
    HELIOS_CHART_FORMATTERS.percent,
  );
}

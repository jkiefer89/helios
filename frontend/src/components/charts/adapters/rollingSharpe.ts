import type { EChartsOption } from "echarts";
import { lineOption } from "./equity";
import { HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export function rollingSharpeOption(points: Array<{ date: string; sharpe: number | null }>): EChartsOption {
  return lineOption(
    points.map((point) => point.date),
    [{
      name: "Rolling Sharpe",
      type: "line",
      data: points.map((point) => point.sharpe),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2, color: toneColor("warning") },
      emphasis: { focus: "series" },
    }],
    HELIOS_CHART_FORMATTERS.ratio,
  );
}

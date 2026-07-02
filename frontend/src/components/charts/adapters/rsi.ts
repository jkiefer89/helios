import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import { chartAlpha, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export type RsiPoint = {
  date: string;
  rsi: number | null;
};

export function rsiOption(points: RsiPoint[]): EChartsOption {
  const series: SeriesOption[] = [
    {
      name: "RSI",
      type: "line",
      data: points.map((point) => point.rsi),
      showSymbol: false,
      lineStyle: { width: 1.8, color: toneColor("info") },
      emphasis: { focus: "series" },
      markLine: {
        silent: true,
        symbol: "none",
        label: { show: false },
        data: [
          { yAxis: 70, lineStyle: { type: "dashed", color: chartAlpha("negative", 0.5) } },
          { yAxis: 30, lineStyle: { type: "dashed", color: chartAlpha("positive", 0.5) } },
        ],
      },
    },
  ];
  const base = lineOption(points.map((point) => point.date), series, HELIOS_CHART_FORMATTERS.number);
  // Pin the axis to the full 0-100 oscillator range so the 70/30 zones stay put.
  return { ...base, yAxis: { ...(base.yAxis as object), min: 0, max: 100, scale: false } };
}

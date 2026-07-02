import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import { chartAlpha, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export type MacdPoint = {
  date: string;
  macd: number | null;
  signal: number | null;
  hist: number | null;
};

export function macdOption(points: MacdPoint[]): EChartsOption {
  const series: SeriesOption[] = [
    {
      name: "Histogram",
      type: "bar",
      data: points.map((point) =>
        point.hist == null
          ? null
          : {
              value: point.hist,
              itemStyle: { color: chartAlpha(point.hist >= 0 ? "positive" : "negative", 0.55) },
            },
      ),
      barCategoryGap: "20%",
      emphasis: { disabled: true },
    },
    {
      name: "MACD",
      type: "line",
      data: points.map((point) => point.macd),
      showSymbol: false,
      lineStyle: { width: 1.6, color: toneColor("info") },
      emphasis: { focus: "series" },
    },
    {
      name: "Signal",
      type: "line",
      data: points.map((point) => point.signal),
      showSymbol: false,
      lineStyle: { width: 1.6, color: toneColor("warning") },
      emphasis: { focus: "series" },
    },
  ];
  return lineOption(points.map((point) => point.date), series, HELIOS_CHART_FORMATTERS.number);
}

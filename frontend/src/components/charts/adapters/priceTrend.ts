import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import { HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

export type PriceTrendPoint = {
  date: string;
  close: number | null;
  sma50?: number | null;
  sma200?: number | null;
};

export function priceTrendOption(points: PriceTrendPoint[]): EChartsOption {
  const series: SeriesOption[] = [
    {
      name: "Close",
      type: "line",
      data: points.map((point) => point.close),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2.2, color: toneColor("info") },
      emphasis: { focus: "series" },
    },
    {
      name: "SMA 50",
      type: "line",
      data: points.map((point) => point.sma50 ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.8, color: toneColor("positive") },
      emphasis: { focus: "series" },
    },
    {
      name: "SMA 200",
      type: "line",
      data: points.map((point) => point.sma200 ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.8, color: toneColor("warning") },
      emphasis: { focus: "series" },
    },
  ];
  return lineOption(points.map((point) => point.date), series, HELIOS_CHART_FORMATTERS.price);
}

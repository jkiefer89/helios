import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import { chartAlpha, chartGlow, chartLegend, HELIOS_CHART_COLORS, HELIOS_CHART_FORMATTERS, HELIOS_CHART_GRID_WITH_LEGEND, toneColor } from "../chartTheme";

export type PriceTrendPoint = {
  date: string;
  close: number | null;
  sma50?: number | null;
  sma200?: number | null;
  bbUpper?: number | null;
  bbLower?: number | null;
};

export type PriceTrendMarker = {
  date: string;
  type: "buy" | "sell";
  price: number;
};

export function priceTrendOption(points: PriceTrendPoint[], markers: PriceTrendMarker[] = []): EChartsOption {
  const dates = points.map((point) => point.date);
  const lows = points.map((point) => (typeof point.bbLower === "number" ? point.bbLower : null));
  const bandWidths = points.map((point) => {
    if (typeof point.bbLower !== "number" || typeof point.bbUpper !== "number") return null;
    return point.bbUpper - point.bbLower;
  });
  const hasBand = bandWidths.some((width) => typeof width === "number");
  const markerSeries = (type: "buy" | "sell"): SeriesOption => ({
    name: type === "buy" ? "Buy signal" : "Sell signal",
    type: "scatter",
    data: markers
      .filter((marker) => marker.type === type && dates.includes(marker.date))
      .map((marker) => [marker.date, marker.price]),
    symbol: "triangle",
    symbolSize: 10,
    symbolRotate: type === "buy" ? 0 : 180,
    itemStyle: { color: toneColor(type === "buy" ? "positive" : "negative") },
    emphasis: { disabled: true },
    z: 5,
  });
  const series: SeriesOption[] = [
    ...(hasBand
      ? ([
          {
            name: "Bollinger base",
            type: "line",
            data: lows,
            showSymbol: false,
            stack: "bollinger-band",
            lineStyle: { opacity: 0 },
            tooltip: { show: false },
            emphasis: { disabled: true },
          },
          {
            name: "Bollinger band",
            type: "line",
            data: bandWidths,
            showSymbol: false,
            stack: "bollinger-band",
            lineStyle: { opacity: 0 },
            areaStyle: { color: chartAlpha("info", 0.07) },
            tooltip: { show: false },
            emphasis: { disabled: true },
          },
        ] satisfies SeriesOption[])
      : []),
    {
      name: "Close",
      type: "line",
      data: points.map((point) => point.close),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2.2, color: HELIOS_CHART_COLORS.text, ...chartGlow("ink", 0.28) },
      itemStyle: { color: HELIOS_CHART_COLORS.text },
      emphasis: { focus: "series" },
    },
    {
      name: "SMA 50",
      type: "line",
      data: points.map((point) => point.sma50 ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.8, color: toneColor("info") },
      itemStyle: { color: toneColor("info") },
      emphasis: { focus: "series" },
    },
    {
      name: "SMA 200",
      type: "line",
      data: points.map((point) => point.sma200 ?? null),
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.8, color: toneColor("warning") },
      itemStyle: { color: toneColor("warning") },
      emphasis: { focus: "series" },
    },
    ...(markers.length ? [markerSeries("buy"), markerSeries("sell")] : []),
  ];
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.price),
    grid: HELIOS_CHART_GRID_WITH_LEGEND,
    legend: chartLegend({ data: ["Close", "SMA 50", "SMA 200"] }),
  };
}

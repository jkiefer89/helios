import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import { chartAlpha, chartLegend, HELIOS_CHART_FORMATTERS, toneColor } from "../chartTheme";

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
  type: "buy" | "sell" | string;
  price: number;
};

export function priceTrendOption(points: PriceTrendPoint[], markers: PriceTrendMarker[] = []): EChartsOption {
  const dates = points.map((point) => point.date);
  const hasBollinger = points.some(
    (point) => typeof point.bbUpper === "number" && typeof point.bbLower === "number",
  );
  const series: SeriesOption[] = [];
  if (hasBollinger) {
    series.push(
      {
        name: "Bollinger lower",
        type: "line",
        data: points.map((point) => point.bbLower ?? null),
        showSymbol: false,
        stack: "bollinger-band",
        lineStyle: { width: 1, color: chartAlpha("info", 0.35) },
        tooltip: { show: false },
        emphasis: { disabled: true },
      },
      {
        name: "Bollinger band",
        type: "line",
        data: points.map((point) =>
          typeof point.bbUpper === "number" && typeof point.bbLower === "number"
            ? point.bbUpper - point.bbLower
            : null,
        ),
        showSymbol: false,
        stack: "bollinger-band",
        lineStyle: { width: 1, color: chartAlpha("info", 0.35) },
        areaStyle: { color: chartAlpha("info", 0.07) },
        tooltip: { show: false },
        emphasis: { disabled: true },
      },
    );
  }
  series.push(
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
  );
  if (markers.length) {
    const buys: Array<number | null> = dates.map(() => null);
    const sells: Array<number | null> = dates.map(() => null);
    markers.forEach((marker) => {
      const index = dates.indexOf(marker.date);
      if (index < 0 || !Number.isFinite(marker.price)) return;
      (marker.type === "buy" ? buys : sells)[index] = marker.price;
    });
    series.push(
      {
        name: "Buy",
        type: "scatter",
        data: buys,
        symbol: "triangle",
        symbolSize: 11,
        itemStyle: { color: toneColor("positive") },
      },
      {
        name: "Sell",
        type: "scatter",
        data: sells,
        symbol: "triangle",
        symbolRotate: 180,
        symbolSize: 11,
        itemStyle: { color: toneColor("negative") },
      },
    );
  }
  return {
    ...lineOption(dates, series, HELIOS_CHART_FORMATTERS.price),
    legend: chartLegend({
      data: ["Close", "SMA 50", "SMA 200"],
    }),
  };
}

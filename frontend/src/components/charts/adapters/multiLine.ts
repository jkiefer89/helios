import type { EChartsOption, SeriesOption } from "echarts";
import { lineOption } from "./equity";
import {
  chartAreaGradient,
  chartGlow,
  chartLegend,
  HELIOS_CHART_GRID_WITH_LEGEND,
  safeChartTone,
  toneColor,
} from "../chartTheme";

export type MultiLineSeries = {
  label: string;
  values: Array<number | null | undefined>;
  tone?: string;
};

/**
 * Generic themed multi-series line chart (Evidence Lab, Signal Journal, …).
 * The first series is the headline: it gets the gradient fill and glow.
 */
export function multiLineOption(labels: string[], series: MultiLineSeries[]): EChartsOption {
  const echartsSeries: SeriesOption[] = series.map((item, index) => {
    const tone = safeChartTone(item.tone || "info");
    const primary = index === 0;
    return {
      name: item.label,
      type: "line",
      data: item.values.map((value) => (typeof value === "number" && Number.isFinite(value) ? value : null)),
      showSymbol: false,
      smooth: true,
      lineStyle: {
        width: primary ? 2.2 : 1.6,
        color: toneColor(tone),
        ...(primary ? chartGlow(tone, 0.32) : {}),
      },
      itemStyle: { color: toneColor(tone) },
      ...(primary ? { areaStyle: { color: chartAreaGradient(tone, 0.16) } } : {}),
      emphasis: { focus: "series" },
    };
  });
  return {
    ...lineOption(labels, echartsSeries),
    ...(series.length > 1
      ? {
          grid: HELIOS_CHART_GRID_WITH_LEGEND,
          legend: chartLegend({ data: series.map((item) => item.label) }),
        }
      : {}),
  };
}

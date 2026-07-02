import type { EChartsOption } from "echarts";
import {
  chartAlpha,
  chartItemTooltip,
  escapeChartHtml,
  HELIOS_CHART_COLORS,
  HELIOS_CHART_FORMATTERS,
  safeChartTone,
  toneColor,
} from "../chartTheme";

export type DonutSegment = {
  label: string;
  value: number;
  tone?: string;
};

/** Thin ring with rounded segment caps, hairline gaps, and hover emphasis. */
export function donutOption(segments: DonutSegment[]): EChartsOption {
  return {
    tooltip: chartItemTooltip((params: unknown) => {
      const item = params as { name?: string; value?: unknown; percent?: number };
      const share = typeof item.percent === "number" ? `${item.percent.toFixed(1)}%` : "n/a";
      return [
        `<strong>${escapeChartHtml(item.name ?? "Segment")}</strong>`,
        `Weight: <strong>${escapeChartHtml(HELIOS_CHART_FORMATTERS.number(item.value))}</strong>`,
        `Share: <strong>${escapeChartHtml(share)}</strong>`,
      ].join("<br/>");
    }),
    legend: { show: false },
    series: [
      {
        type: "pie",
        radius: ["72%", "92%"],
        center: ["50%", "50%"],
        avoidLabelOverlap: false,
        padAngle: 2,
        itemStyle: {
          borderRadius: 4,
          borderColor: HELIOS_CHART_COLORS.panel,
          borderWidth: 1,
        },
        label: { show: false },
        labelLine: { show: false },
        emphasis: {
          scale: true,
          scaleSize: 4,
          itemStyle: { shadowBlur: 12, shadowColor: chartAlpha("ink", 0.25) },
        },
        data: segments.map((segment) => ({
          name: segment.label,
          value: segment.value,
          itemStyle: { color: toneColor(safeChartTone(segment.tone || "info")) },
        })),
      },
    ],
  };
}

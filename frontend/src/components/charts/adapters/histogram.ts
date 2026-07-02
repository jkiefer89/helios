import type { EChartsOption } from "echarts";
import {
  chartAlpha,
  chartAreaGradient,
  chartCategoryAxis,
  chartGuides,
  chartTooltip,
  chartValueAxis,
  escapeChartHtml,
  HELIOS_CHART_COLORS,
  HELIOS_CHART_FORMATTERS,
  safeChartTone,
} from "../chartTheme";

export type HistogramOptions = {
  label?: string;
  buckets?: number;
  tone?: string;
  min?: number;
  max?: number;
};

/** Themed distribution histogram: rounded gradient bars + zero/median reference. */
export function histogramOption(values: number[], options: HistogramOptions = {}): EChartsOption {
  const tone = safeChartTone(options.tone || "info");
  const label = options.label ?? "Value";
  const bucketCount = Math.max(3, Math.min(12, Math.round(options.buckets ?? 8)));
  const floor = typeof options.min === "number" ? options.min : Math.min(...values);
  const ceiling = typeof options.max === "number" ? options.max : Math.max(...values);
  const span = ceiling - floor || 1;
  const counts = Array.from({ length: bucketCount }, () => 0);
  values.forEach((value) => {
    const rawIndex = Math.floor(((value - floor) / span) * bucketCount);
    counts[Math.max(0, Math.min(bucketCount - 1, rawIndex))] += 1;
  });
  const bucketStart = (index: number) => floor + (span / bucketCount) * index;
  const categories = counts.map((_, index) => HELIOS_CHART_FORMATTERS.number(bucketStart(index)));

  // Reference line: zero when the range straddles it, otherwise the median bucket.
  const sorted = [...values].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const useZero = floor < 0 && ceiling > 0;
  const referenceValue = useZero ? 0 : median;
  const referenceIndex = Math.max(0, Math.min(bucketCount - 1, Math.floor(((referenceValue - floor) / span) * bucketCount)));

  return {
    tooltip: {
      ...chartTooltip(HELIOS_CHART_FORMATTERS.count),
      axisPointer: { type: "shadow" as const, shadowStyle: { color: chartAlpha(tone, 0.06) } },
      formatter: (params: unknown) => {
        const row = (Array.isArray(params) ? params[0] : params) as { dataIndex?: number; value?: unknown } | undefined;
        const index = typeof row?.dataIndex === "number" ? row.dataIndex : 0;
        const range = `${HELIOS_CHART_FORMATTERS.number(bucketStart(index))} to ${HELIOS_CHART_FORMATTERS.number(bucketStart(index + 1))}`;
        const count = HELIOS_CHART_FORMATTERS.count(row?.value);
        return `${escapeChartHtml(label)} ${escapeChartHtml(range)}<br/><strong>${escapeChartHtml(count)}</strong> observations`;
      },
    },
    xAxis: chartCategoryAxis(categories, {
      boundaryGap: true,
      axisLabel: {
        color: HELIOS_CHART_COLORS.muted,
        fontSize: 9,
        fontWeight: 600,
        hideOverlap: true,
        margin: 10,
        formatter: (value: string) => value,
      },
    }),
    yAxis: chartValueAxis(HELIOS_CHART_FORMATTERS.count, { scale: false, minInterval: 1 }),
    series: [
      {
        name: label,
        type: "bar",
        data: counts,
        barCategoryGap: "26%",
        itemStyle: {
          borderRadius: [3, 3, 0, 0],
          color: chartAreaGradient(tone, 0.85, 0.25),
        },
        emphasis: {
          itemStyle: { color: chartAreaGradient(tone, 1, 0.4) },
        },
        markLine: {
          ...chartGuides([]),
          data: [
            {
              xAxis: referenceIndex,
              lineStyle: { color: chartAlpha(useZero ? "neutral" : "warning", 0.5), type: [4, 4] as number[], width: 1 },
              label: {
                show: true,
                formatter: useZero ? "0" : "MEDIAN",
                position: "insideEndTop" as const,
                color: chartAlpha(useZero ? "neutral" : "warning", 0.9),
                fontSize: 9,
                fontWeight: 700,
              },
            },
          ],
        },
      },
    ],
  };
}

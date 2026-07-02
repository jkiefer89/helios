import type { EChartsOption } from "echarts";
import {
  chartAlpha,
  chartItemTooltip,
  chartValueAxis,
  escapeChartHtml,
  HELIOS_CHART_COLORS,
  HELIOS_CHART_FORMATTERS,
  safeChartTone,
  toneColor,
} from "../chartTheme";

export type ScoreScatterPoint = {
  label: string;
  x: number;
  y: number;
  size?: number;
  tone?: string;
  meta?: string;
};

type ScatterDatum = {
  name?: string;
  value?: [number, number];
  meta?: string;
};

function pointTone(point: ScoreScatterPoint) {
  return safeChartTone(point.tone || (point.y >= 70 ? "positive" : point.y >= 50 ? "warning" : "neutral"));
}

/** 0-100 score map: sized/tone-encoded points, quadrant guides, hover emphasis. */
export function scoreScatterOption(points: ScoreScatterPoint[], xLabel: string, yLabel: string): EChartsOption {
  const axisName = (name: string) => ({
    name: name.toUpperCase(),
    nameLocation: "middle" as const,
    nameTextStyle: { color: HELIOS_CHART_COLORS.muted, fontSize: 9, fontWeight: 700 as const },
  });
  return {
    tooltip: chartItemTooltip((params: unknown) => {
      const datum = (params as { data?: ScatterDatum }).data ?? {};
      const [x, y] = datum.value ?? [null, null];
      const rows = [
        `<strong>${escapeChartHtml(datum.name ?? "Candidate")}</strong>`,
        `${escapeChartHtml(yLabel)}: <strong>${escapeChartHtml(HELIOS_CHART_FORMATTERS.number(y))}</strong>`,
        `${escapeChartHtml(xLabel)}: <strong>${escapeChartHtml(HELIOS_CHART_FORMATTERS.number(x))}</strong>`,
      ];
      if (datum.meta) rows.push(escapeChartHtml(datum.meta));
      return rows.join("<br/>");
    }),
    grid: { left: 16, right: 16, top: 16, bottom: 24, containLabel: true },
    xAxis: chartValueAxis(HELIOS_CHART_FORMATTERS.count, {
      scale: false,
      min: 0,
      max: 100,
      nameGap: 26,
      ...axisName(xLabel),
    }),
    yAxis: chartValueAxis(HELIOS_CHART_FORMATTERS.count, {
      scale: false,
      min: 0,
      max: 100,
      nameGap: 34,
      ...axisName(yLabel),
    }),
    series: [
      {
        name: yLabel,
        type: "scatter",
        data: points.map((point) => ({
          name: point.label,
          value: [point.x, point.y] as [number, number],
          meta: point.meta,
          symbolSize: Math.max(8, Math.min(22, (point.size ?? 7) * 2)),
          itemStyle: {
            color: chartAlpha(pointTone(point), 0.82),
            borderColor: toneColor(pointTone(point)),
            borderWidth: 1,
            shadowBlur: 6,
            shadowColor: chartAlpha(pointTone(point), 0.35),
          },
        })),
        emphasis: {
          scale: 1.35,
          itemStyle: { shadowBlur: 14 },
          label: {
            show: true,
            position: "top" as const,
            formatter: (params: { name?: string }) => params.name ?? "",
            color: HELIOS_CHART_COLORS.text,
            fontSize: 10,
            fontWeight: 700,
          },
        },
        // Quadrant guides at the 50/50 midpoint.
        markLine: {
          silent: true,
          symbol: "none",
          animation: false,
          lineStyle: { color: chartAlpha("neutral", 0.3), type: [4, 4] as number[], width: 1 },
          label: { show: false },
          data: [{ xAxis: 50 }, { yAxis: 50 }],
        },
      },
    ],
  };
}

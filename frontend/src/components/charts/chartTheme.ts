import type { EChartsOption } from "echarts";

type ChartTone = "positive" | "negative" | "warning" | "info" | "neutral";
/** Tones plus the two non-semantic chart inks (violet accent, primary ink). */
type ChartPaint = ChartTone | "accent" | "ink";
type TooltipDatum = {
  marker?: string;
  seriesName?: string;
  value?: unknown;
  data?: unknown;
  axisValueLabel?: unknown;
  axisValue?: unknown;
  name?: unknown;
};
type ChartValueFormatter = (value: unknown) => string;

export const HELIOS_CHART_COLORS = {
  positive: "#3ecf80",
  negative: "#ff6b74",
  warning: "#eab660",
  info: "#5c9dff",
  neutral: "#93a0b4",
  accent: "#a98ffa",
  panel: "#0a0f17",
  grid: "rgba(158, 176, 202, 0.12)",
  axis: "rgba(158, 176, 202, 0.2)",
  text: "#c7d1df",
  muted: "#8d9ab0",
};

const HELIOS_CHART_COLOR_CHANNELS: Record<ChartPaint, string> = {
  positive: "62, 207, 128",
  negative: "255, 107, 116",
  warning: "234, 182, 96",
  info: "92, 157, 255",
  neutral: "147, 160, 180",
  accent: "169, 143, 250",
  ink: "199, 209, 223",
};

export const HELIOS_CHART_FORMATTERS = {
  number(value: unknown): string {
    const number = finiteNumber(value);
    if (number == null) return "n/a";
    return number.toLocaleString(undefined, { maximumFractionDigits: 2 });
  },
  ratio(value: unknown): string {
    const number = finiteNumber(value);
    if (number == null) return "n/a";
    return number.toFixed(Math.abs(number) >= 10 ? 1 : 2);
  },
  percent(value: unknown): string {
    const number = finiteNumber(value);
    if (number == null) return "n/a";
    return `${number >= 0 ? "+" : ""}${number.toFixed(1)}%`;
  },
  price(value: unknown): string {
    const number = finiteNumber(value);
    if (number == null) return "n/a";
    return `$${number.toLocaleString(undefined, { maximumFractionDigits: Math.abs(number) >= 100 ? 2 : 4 })}`;
  },
  count(value: unknown): string {
    const number = finiteNumber(value);
    if (number == null) return "n/a";
    return Math.round(number).toLocaleString();
  },
  date(value: unknown): string {
    if (typeof value !== "string" || !value.trim()) return "n/a";
    const date = new Date(value);
    if (!Number.isFinite(date.valueOf())) return value;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  },
} satisfies Record<string, ChartValueFormatter>;

const HELIOS_AXIS_LABEL = {
  color: HELIOS_CHART_COLORS.muted,
  fontSize: 10,
  fontWeight: 600,
  fontFamily:
    'ui-monospace, "SF Mono", SFMono-Regular, "Cascadia Code", "Segoe UI Mono", "Roboto Mono", Menlo, Consolas, monospace',
} as const;

/** Dashed crosshair with dark value chips; y chip honours the chart's own formatter. */
function chartAxisPointer(formatValue: ChartValueFormatter = HELIOS_CHART_FORMATTERS.number) {
  const pointerLine = { color: chartAlpha("info", 0.4), type: [3, 3] as number[], width: 1 };
  return {
    type: "cross" as const,
    lineStyle: pointerLine,
    crossStyle: pointerLine,
    label: {
      backgroundColor: "rgba(8, 12, 19, 0.95)",
      borderColor: "rgba(92, 157, 255, 0.35)",
      borderWidth: 1,
      color: HELIOS_CHART_COLORS.text,
      fontSize: 10,
      fontWeight: 600,
      fontFamily:
        'ui-monospace, "SF Mono", SFMono-Regular, "Cascadia Code", "Segoe UI Mono", "Roboto Mono", Menlo, Consolas, monospace',
      padding: [3, 7],
      formatter: (params: { axisDimension?: string; value?: unknown }) =>
        params.axisDimension === "y"
          ? formatValue(params.value)
          : HELIOS_CHART_FORMATTERS.date(String(params.value ?? "")).toUpperCase(),
    },
  };
}

const HELIOS_CHART_TOOLTIP = {
  trigger: "axis",
  backgroundColor: "rgba(7, 11, 18, 0.95)",
  borderColor: "rgba(92, 157, 255, 0.3)",
  borderWidth: 1,
  padding: [8, 11] as number[],
  textStyle: { color: HELIOS_CHART_COLORS.text, fontSize: 11 },
  extraCssText: "border-radius: 8px; box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5); backdrop-filter: blur(6px);",
  confine: true,
} as const;

const HELIOS_CHART_LEGEND = {
  show: true,
  bottom: 0,
  icon: "roundRect",
  itemWidth: 14,
  itemHeight: 4,
  itemGap: 16,
  textStyle: { color: HELIOS_CHART_COLORS.muted, fontSize: 10, fontWeight: 600 },
  inactiveColor: "rgba(148, 163, 184, 0.3)",
} as const;

export const HELIOS_CHART_GRID = {
  left: 10,
  right: 14,
  top: 24,
  bottom: 8,
  containLabel: true,
} as const;

/** Grid variant leaving room for the curated bottom legend strip. */
export const HELIOS_CHART_GRID_WITH_LEGEND = {
  ...HELIOS_CHART_GRID,
  bottom: 30,
} as const;

export const HELIOS_CHART_THEME: EChartsOption = {
  color: [
    HELIOS_CHART_COLORS.info,
    HELIOS_CHART_COLORS.positive,
    HELIOS_CHART_COLORS.warning,
    HELIOS_CHART_COLORS.negative,
    HELIOS_CHART_COLORS.neutral,
  ],
  backgroundColor: "transparent",
  animationDuration: 400,
  animationEasing: "cubicOut",
  animationDurationUpdate: 240,
  animationEasingUpdate: "cubicOut",
  textStyle: {
    color: HELIOS_CHART_COLORS.text,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    fontSize: 11,
  },
  tooltip: { ...HELIOS_CHART_TOOLTIP, axisPointer: chartAxisPointer() },
  grid: HELIOS_CHART_GRID,
  // Legends are curated per adapter; single-series charts stay clutter-free.
  legend: { ...HELIOS_CHART_LEGEND, show: false },
};

export function chartAlpha(tone: ChartPaint, alpha: number) {
  const safeAlpha = Math.max(0, Math.min(1, alpha));
  return `rgba(${HELIOS_CHART_COLOR_CHANNELS[tone]}, ${safeAlpha})`;
}

/** Vertical gradient fill fading to transparent under a primary series. */
export function chartAreaGradient(tone: ChartPaint, from = 0.26, to = 0) {
  return {
    type: "linear" as const,
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: chartAlpha(tone, from) },
      { offset: 1, color: chartAlpha(tone, to) },
    ],
  };
}

/** Subtle self-coloured glow for a chart's primary line only. */
export function chartGlow(tone: ChartPaint, alpha = 0.38) {
  return {
    shadowBlur: 9,
    shadowColor: chartAlpha(tone, alpha),
    shadowOffsetY: 3,
  };
}

/** Dashed horizontal reference lines (zero, thresholds) via markLine. */
export function chartGuides(guides: Array<{ value: number; tone?: ChartPaint; label?: string }>) {
  return {
    silent: true,
    symbol: "none",
    animation: false,
    data: guides.map((guide) => ({
      yAxis: guide.value,
      lineStyle: { color: chartAlpha(guide.tone ?? "neutral", 0.42), type: [4, 4] as number[], width: 1 },
      label: {
        show: Boolean(guide.label),
        formatter: guide.label ?? "",
        position: "insideEndTop" as const,
        color: chartAlpha(guide.tone ?? "neutral", 0.85),
        fontSize: 9,
        fontWeight: 700,
      },
    })),
  };
}

/** Category (date) x-axis: no hard axis line, small uppercase muted labels. */
export function chartCategoryAxis(dates: string[], overrides: Record<string, unknown> = {}) {
  return {
    type: "category" as const,
    data: dates,
    boundaryGap: false,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      ...HELIOS_AXIS_LABEL,
      hideOverlap: true,
      margin: 12,
      formatter: (value: string | number) => HELIOS_CHART_FORMATTERS.date(String(value)).toUpperCase(),
    },
    ...overrides,
  };
}

/** Value y-axis: sparse low-opacity dashed gridlines, no axis line. */
export function chartValueAxis(
  formatter: ChartValueFormatter = HELIOS_CHART_FORMATTERS.number,
  overrides: Record<string, unknown> = {},
) {
  return {
    type: "value" as const,
    scale: true,
    splitNumber: 4,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { ...HELIOS_AXIS_LABEL, formatter },
    splitLine: { lineStyle: { color: HELIOS_CHART_COLORS.grid, type: [2, 6] as number[] } },
    ...overrides,
  };
}

export function chartTooltip(formatValue: ChartValueFormatter = HELIOS_CHART_FORMATTERS.number) {
  return {
    ...HELIOS_CHART_TOOLTIP,
    axisPointer: chartAxisPointer(formatValue),
    formatter: axisTooltipFormatter(formatValue),
  };
}

/** Item-trigger variant of the glass tooltip (scatter, pie). */
export function chartItemTooltip(formatter?: (params: unknown) => string) {
  return {
    ...HELIOS_CHART_TOOLTIP,
    trigger: "item" as const,
    ...(formatter ? { formatter } : {}),
  };
}

export function chartLegend(overrides: Record<string, unknown> = {}) {
  return {
    ...HELIOS_CHART_LEGEND,
    ...overrides,
  };
}

export function axisTooltipFormatter(formatValue: ChartValueFormatter = HELIOS_CHART_FORMATTERS.number) {
  return (params: unknown) => {
    const rows = (Array.isArray(params) ? params : [params]).filter(isTooltipDatum);
    const first = rows[0];
    const label = first ? first.axisValueLabel ?? first.axisValue ?? first.name : undefined;
    const header = typeof label === "string" ? escapeHtml(HELIOS_CHART_FORMATTERS.date(label)) : "";
    const body = rows
      .map((row) => {
        const name = typeof row.seriesName === "string" ? row.seriesName : "Series";
        const value = formatValue(extractTooltipValue(row.value ?? row.data));
        return `${row.marker ?? ""}${escapeHtml(name)}: <strong>${escapeHtml(value)}</strong>`;
      })
      .join("<br/>");
    return [header, body].filter(Boolean).join("<br/>");
  };
}

export function toneColor(tone?: string) {
  return HELIOS_CHART_COLORS[safeChartTone(tone)];
}

export function safeChartTone(tone?: string): ChartTone {
  if (tone === "positive" || tone === "negative" || tone === "warning" || tone === "info" || tone === "neutral") {
    return tone;
  }
  return "neutral";
}

export function escapeChartHtml(value: string): string {
  return escapeHtml(value);
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function extractTooltipValue(value: unknown): unknown {
  if (Array.isArray(value)) return value[value.length - 1];
  if (value && typeof value === "object" && "value" in value) return (value as { value: unknown }).value;
  return value;
}

function isTooltipDatum(value: unknown): value is TooltipDatum {
  return Boolean(value && typeof value === "object");
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

import type { EChartsOption } from "echarts";

type ChartTone = "positive" | "negative" | "warning" | "info" | "neutral";
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
  positive: "#47d66f",
  negative: "#ff5c67",
  warning: "#f4c542",
  info: "#4c9dff",
  neutral: "#9aa8ba",
  panel: "#0b1522",
  grid: "rgba(148, 163, 184, 0.16)",
  axis: "rgba(148, 163, 184, 0.22)",
  text: "#cbd7e6",
  muted: "#8fa1b6",
};

const HELIOS_CHART_COLOR_CHANNELS: Record<ChartTone, string> = {
  positive: "71, 214, 111",
  negative: "255, 92, 103",
  warning: "244, 197, 66",
  info: "76, 157, 255",
  neutral: "154, 168, 186",
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
  date(value: unknown): string {
    if (typeof value !== "string" || !value.trim()) return "n/a";
    const date = new Date(value);
    if (!Number.isFinite(date.valueOf())) return value;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  },
} satisfies Record<string, ChartValueFormatter>;

const HELIOS_CHART_TOOLTIP = {
  trigger: "axis",
  backgroundColor: "rgba(7, 12, 20, 0.96)",
  borderColor: "rgba(76, 157, 255, 0.35)",
  borderWidth: 1,
  textStyle: { color: HELIOS_CHART_COLORS.text },
  confine: true,
} as const;

const HELIOS_CHART_LEGEND = {
  bottom: 0,
  icon: "roundRect",
  itemWidth: 16,
  itemHeight: 3,
  textStyle: { color: HELIOS_CHART_COLORS.text },
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
  textStyle: {
    color: HELIOS_CHART_COLORS.text,
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: 11,
  },
  tooltip: HELIOS_CHART_TOOLTIP,
  grid: {
    left: 44,
    right: 18,
    top: 22,
    bottom: 34,
    containLabel: true,
  },
  legend: HELIOS_CHART_LEGEND,
};

export function chartAlpha(tone: ChartTone, alpha: number) {
  const safeAlpha = Math.max(0, Math.min(1, alpha));
  return `rgba(${HELIOS_CHART_COLOR_CHANNELS[tone]}, ${safeAlpha})`;
}

export function chartTooltip(formatValue: ChartValueFormatter = HELIOS_CHART_FORMATTERS.number) {
  return {
    ...HELIOS_CHART_TOOLTIP,
    formatter: axisTooltipFormatter(formatValue),
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
  if (tone === "positive" || tone === "negative" || tone === "warning" || tone === "info" || tone === "neutral") {
    return HELIOS_CHART_COLORS[tone];
  }
  return HELIOS_CHART_COLORS.neutral;
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

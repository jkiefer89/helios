export function fmtNumber(value: unknown, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
}

export function fmtPct(value: unknown, digits = 1): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

export function fmtMoney(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function sourceSummary(sourceCounts?: Record<string, number>, weights?: Record<string, number>): string {
  const countParts = sourceCounts
    ? Object.entries(sourceCounts).map(([key, value]) => `${titleCase(key)} ${value}`)
    : [];
  if (countParts.length) return countParts.join(" · ");
  const weightParts = weights
    ? Object.entries(weights).map(([key, value]) => `${titleCase(key)} ${fmtNumber(value, 1)}%`)
    : [];
  return weightParts.join(" · ");
}

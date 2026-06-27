import { fmtNumber } from "../../utils/format";

type ChartTone = "positive" | "negative" | "warning" | "info" | "neutral";

function safeTone(tone?: string): ChartTone {
  if (tone === "positive" || tone === "negative" || tone === "warning" || tone === "info" || tone === "neutral") return tone;
  return "neutral";
}

export function ScoreBar({ value, tone = "positive" }: { value?: number; tone?: string }) {
  const safe = Math.max(0, Math.min(100, Number(value) || 0));
  const toneClass = safeTone(tone);
  return (
    <div className="score-bar" aria-label={`Score ${fmtNumber(safe, 1)} out of 100`}>
      <span className={`score-bar__fill tone-${toneClass}`} style={{ width: `${safe}%` }} />
      <b>{fmtNumber(safe, 1)}</b>
    </div>
  );
}

export function MiniBars({ rows }: { rows: Array<{ label: string; value: number; tone?: string }> }) {
  if (!rows.length) return <div className="chart-empty">No chartable real-data rows.</div>;
  return (
    <div className="mini-bars">
      {rows.map((row) => {
        const safe = Math.max(0, Math.min(100, Number(row.value) || 0));
        const toneClass = safeTone(row.tone || "positive");
        return (
          <div className="mini-bars__row" key={row.label}>
            <span>{row.label}</span>
            <div><i className={`tone-${toneClass}`} style={{ width: `${safe}%` }} /></div>
            <b>{fmtNumber(row.value, 1)}</b>
          </div>
        );
      })}
    </div>
  );
}

export function LineChart({
  labels,
  series,
  height = 180,
}: {
  labels: string[];
  series: Array<{ label: string; values: Array<number | null | undefined>; tone?: string }>;
  height?: number;
}) {
  const points = series.flatMap((item) => item.values.filter((value): value is number => typeof value === "number" && Number.isFinite(value)));
  if (labels.length < 2 || points.length < 2) return <div className="chart-empty">Chart appears when enough history is available.</div>;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const width = 640;
  const pad = 12;
  const toPoint = (value: number, index: number) => {
    const x = pad + (index / Math.max(1, labels.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (value - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };
  const segmentsFor = (values: Array<number | null | undefined>) => {
    const segments: string[][] = [];
    let current: string[] = [];
    labels.forEach((_, index) => {
      const value = values[index];
      if (typeof value === "number" && Number.isFinite(value)) {
        current.push(toPoint(value, index));
        return;
      }
      if (current.length > 1) segments.push(current);
      current = [];
    });
    if (current.length > 1) segments.push(current);
    return segments;
  };
  return (
    <div className="line-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Historical line chart" style={{ height }}>
        <g className="grid-lines">
          {[0.25, 0.5, 0.75].map((pct) => <line key={pct} x1="0" x2={width} y1={height * pct} y2={height * pct} />)}
        </g>
        {series.flatMap((item) => {
          const toneClass = safeTone(item.tone || "info");
          return segmentsFor(item.values).map((segment, index) => (
            <polyline key={`${item.label}-${index}`} className={`line-chart__line tone-${toneClass}`} points={segment.join(" ")} />
          ));
        })}
      </svg>
      <div className="chart-legend">
        {series.map((item) => <span key={item.label} className={`tone-${safeTone(item.tone || "info")}`}>{item.label}</span>)}
      </div>
    </div>
  );
}

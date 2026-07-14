type ChartStateProps = {
  title?: string;
  body: string;
  kind?: "empty" | "loading" | "error" | "locked" | "live";
  minHeight?: number;
};

export function ChartState({ title = "Chart unavailable", body, kind = "empty", minHeight }: ChartStateProps) {
  return (
    <div
      className={`chart-empty chart-state chart-state--${kind}`}
      role={kind === "error" ? "alert" : "status"}
      aria-busy={kind === "loading" ? true : undefined}
      style={minHeight ? { minHeight } : undefined}
    >
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

export function EmptyChartState({ body = "Chart appears when enough history is available.", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="No chartable data" body={body} minHeight={minHeight} />;
}

export function LoadingChartState({ body = "Loading chart renderer...", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="Loading chart" body={body} kind="loading" minHeight={minHeight} />;
}

type ChartStateProps = {
  title?: string;
  body: string;
  kind?: "empty" | "loading" | "error" | "locked" | "demo" | "live";
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

export function ErrorChartState({ body = "Chart could not be rendered.", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="Chart unavailable" body={body} kind="error" minHeight={minHeight} />;
}

export function LockedChartState({ body = "Chart is locked until more evidence is available.", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="Chart locked" body={body} kind="locked" minHeight={minHeight} />;
}

export function DemoChartState({ body = "Demo data is separated from live analysis.", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="Demo chart" body={body} kind="demo" minHeight={minHeight} />;
}

export function LiveChartState({ body = "Live chart data is loading.", minHeight }: { body?: string; minHeight?: number }) {
  return <ChartState title="Live chart" body={body} kind="live" minHeight={minHeight} />;
}

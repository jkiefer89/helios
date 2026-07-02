import type { EChartsOption } from "echarts";
import {
  BarChart as EChartsBarChart,
  LineChart as EChartsLineChart,
  PieChart as EChartsPieChart,
  ScatterChart as EChartsScatterChart,
} from "echarts/charts";
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { HELIOS_CHART_THEME } from "./chartTheme";

echarts.use([
  GridComponent,
  LegendComponent,
  TooltipComponent,
  MarkLineComponent,
  EChartsLineChart,
  EChartsBarChart,
  EChartsScatterChart,
  EChartsPieChart,
  SVGRenderer,
  CanvasRenderer,
]);

type HeliosEChartProps = {
  option: EChartsOption;
  height?: number;
  renderer?: "svg" | "canvas";
  ariaLabel?: string;
};

/** Entrance/update animations stay off for users who ask for reduced motion. */
function withTheme(option: EChartsOption): EChartsOption {
  const reduceMotion =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return { ...HELIOS_CHART_THEME, ...option, ...(reduceMotion ? { animation: false } : {}) };
}

// Drives echarts/core directly instead of echarts-for-react: that wrapper is
// CJS-only and its default import breaks under Vite 8's ESM interop.
export function HeliosEChart({
  option,
  height = 240,
  renderer = "svg",
  ariaLabel = "Helios chart",
}: HeliosEChartProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.EChartsType | null>(null);
  const latestOption = useRef(option);
  latestOption.current = option;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = echarts.init(host, undefined, { renderer });
    chart.setOption(withTheme(latestOption.current), { notMerge: true });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(host);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [renderer]);

  useEffect(() => {
    chartRef.current?.setOption(withTheme(option), { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={hostRef} className="helios-echart" role="img" aria-label={ariaLabel} style={{ height, width: "100%" }} />;
}

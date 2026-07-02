import type { EChartsOption } from "echarts";
import { BarChart as EChartsBarChart, LineChart as EChartsLineChart, ScatterChart as EChartsScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { HELIOS_CHART_THEME } from "./chartTheme";

echarts.use([GridComponent, LegendComponent, TooltipComponent, EChartsLineChart, EChartsBarChart, EChartsScatterChart, SVGRenderer, CanvasRenderer]);

type HeliosEChartProps = {
  option: EChartsOption;
  height?: number;
  renderer?: "svg" | "canvas";
  ariaLabel?: string;
};

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
    chart.setOption({ ...HELIOS_CHART_THEME, ...latestOption.current }, { notMerge: true });
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
    chartRef.current?.setOption({ ...HELIOS_CHART_THEME, ...option }, { notMerge: true, lazyUpdate: true });
  }, [option]);

  return <div ref={hostRef} className="helios-echart" role="img" aria-label={ariaLabel} style={{ height, width: "100%" }} />;
}

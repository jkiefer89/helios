import type { EChartsOption } from "echarts";
import { BarChart as EChartsBarChart, LineChart as EChartsLineChart, ScatterChart as EChartsScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";
import ReactECharts from "echarts-for-react/lib/core";
import { HELIOS_CHART_THEME } from "./chartTheme";

echarts.use([GridComponent, LegendComponent, TooltipComponent, EChartsLineChart, EChartsBarChart, EChartsScatterChart, SVGRenderer, CanvasRenderer]);

type HeliosEChartProps = {
  option: EChartsOption;
  height?: number;
  renderer?: "svg" | "canvas";
  ariaLabel?: string;
};

export function HeliosEChart({
  option,
  height = 240,
  renderer = "svg",
  ariaLabel = "Helios chart",
}: HeliosEChartProps) {
  return (
    <div className="helios-echart" role="img" aria-label={ariaLabel} style={{ height }}>
      <ReactECharts
        echarts={echarts}
        option={{ ...HELIOS_CHART_THEME, ...option }}
        notMerge
        lazyUpdate
        style={{ width: "100%", height }}
        opts={{ renderer }}
      />
    </div>
  );
}

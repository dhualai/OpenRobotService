// 按需引入 echarts，避免整包（~1.1MB）打入 bundle。
// 仅注册当前页面实际用到的图表：柱状图（bar）、饼图（pie）。
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { BarChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsReactProps } from 'echarts-for-react';

echarts.use([
  BarChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

/**
 * 精简版 echarts React 组件：自动注入按需构建的 echarts 实例，
 * 用法与原 echarts-for-react 默认导出一致，调用方无需传 echarts。
 */
export default function ReactECharts(props: EChartsReactProps) {
  return <ReactEChartsCore echarts={echarts} {...props} />;
}

export { echarts };

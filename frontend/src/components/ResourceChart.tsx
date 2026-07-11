/**
 * 资源指标折线图(T1.18)。基于 ECharts 画单卡曲线,数据来自 Prometheus
 * query_range 转换出的 LineSeries。克制配色:主色点缀、浅网格、小字号,
 * 对齐设计规范(非花哨仪表盘)。
 *
 * 图表渲染细节交给 echarts-for-react;本组件只把 LineSeries 映射成 option,
 * 并处理加载/空/错误三态,便于页面聚合多张卡。
 */

import ReactECharts from "echarts-for-react";
import { Empty, Skeleton } from "antd";

import type { LineSeries } from "@/api/metricsTransform";
import { colors } from "@/theme";

interface ResourceChartProps {
  title: string;
  series: LineSeries[];
  loading?: boolean;
  error?: string | null;
  /** y 轴单位后缀,如 "%";留空则不加。 */
  unit?: string;
  height?: number;
}

const PALETTE = [colors.primary, colors.info, colors.warning, colors.danger, "#9B59B6"];

export function ResourceChart({
  title,
  series,
  loading = false,
  error = null,
  unit = "",
  height = 220,
}: ResourceChartProps): React.ReactElement {
  if (loading) {
    return <Skeleton active paragraph={{ rows: 4 }} title={false} />;
  }
  if (error) {
    return <Empty description={error} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }
  const hasData = series.some((s) => s.points.length > 0);
  if (!hasData) {
    return <Empty description="暂无指标数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  const option = {
    color: PALETTE,
    title: {
      text: title,
      textStyle: { fontSize: 13, fontWeight: 600, color: colors.textTitle },
      left: 0,
      top: 0,
    },
    grid: { top: 36, right: 16, bottom: 28, left: 48 },
    tooltip: { trigger: "axis" },
    legend: series.length > 1 ? { top: 0, right: 0, textStyle: { fontSize: 11 } } : undefined,
    xAxis: {
      type: "time",
      axisLabel: { fontSize: 11, color: colors.textBody },
      axisLine: { lineStyle: { color: colors.cardBorder } },
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 11, color: colors.textBody, formatter: `{value}${unit}` },
      splitLine: { lineStyle: { color: "#F0F0F0" } },
    },
    series: series.map((s) => ({
      name: s.name,
      type: "line",
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.5 },
      data: s.points.map((p) => [p.t, p.v]),
    })),
  };

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

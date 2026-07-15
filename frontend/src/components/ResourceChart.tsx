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

/** 部署标注:一条竖线打在部署时间点上(§9.2「运维最爱」),看"发布后曲线变化"。 */
export interface DeployMarker {
  /** 部署时间(毫秒时间戳,对齐 xAxis type:time)。 */
  t: number;
  /** 悬浮标签,如 "v1.2.0 · 张三"。 */
  label: string;
}

interface ResourceChartProps {
  title: string;
  series: LineSeries[];
  loading?: boolean;
  error?: string | null;
  /** y 轴单位后缀,如 "%";留空则不加。 */
  unit?: string;
  height?: number;
  /** 部署时间点标注:在图上按时间打竖线(§9.2)。 */
  markers?: DeployMarker[];
}

const PALETTE = [colors.primary, colors.info, colors.warning, colors.danger, colors.chartViolet];

export function ResourceChart({
  title,
  series,
  loading = false,
  error = null,
  unit = "",
  height = 220,
  markers = [],
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
      splitLine: { lineStyle: { color: colors.chartSplitLine } },
    },
    series: series.map((s, idx) => ({
      name: s.name,
      type: "line",
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 1.5 },
      data: s.points.map((p) => [p.t, p.v]),
      // 部署标注只挂到第一条 series,避免多条线重复画竖线(§9.2 部署时间点竖线)
      markLine:
        idx === 0 && markers.length > 0
          ? {
              symbol: "none",
              silent: false,
              lineStyle: { color: colors.primary, type: "dashed", width: 1 },
              label: {
                fontSize: 10,
                color: colors.primary,
                formatter: (params: { name?: string }) => params.name ?? "部署",
              },
              data: markers.map((m) => ({ xAxis: m.t, name: m.label })),
            }
          : undefined,
    })),
  };

  return <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />;
}

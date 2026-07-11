/**
 * Prometheus matrix 结果 → ECharts 折线序列的纯转换(T1.18)。
 *
 * 独立成文件便于单测:不依赖 React/ECharts 运行时,只做数据形状转换。
 * matrix 的每条 result 是一个时间序列,values 为 [unix_ts(秒), 字符串值] 数组。
 */

import type { PromData, PromMatrixSample } from "./metrics";

export interface LinePoint {
  /** 毫秒时间戳,供 ECharts time 轴。 */
  t: number;
  /** 数值;Prometheus 传字符串,这里转 number(NaN 会被过滤)。 */
  v: number;
}

export interface LineSeries {
  name: string;
  points: LinePoint[];
}

/** 从 metric 标签取一个可读的序列名:优先 instance,退而取 __name__,再退到全部标签串。 */
export function seriesName(metric: Record<string, string>): string {
  if (metric.instance) return metric.instance;
  if (metric.__name__) return metric.__name__;
  const entries = Object.entries(metric);
  if (entries.length === 0) return "series";
  return entries.map(([k, v]) => `${k}=${v}`).join(",");
}

function toPoints(sample: PromMatrixSample): LinePoint[] {
  return sample.values
    .map(([ts, raw]) => ({ t: ts * 1000, v: Number(raw) }))
    .filter((p) => Number.isFinite(p.v));
}

/**
 * 把 query_range 的 matrix 响应转成折线序列数组。
 * 非 matrix(如后端在异常时给了空 vector)返回空数组,由调用方显示空态。
 */
export function matrixToSeries(data: PromData): LineSeries[] {
  if (data.resultType !== "matrix") return [];
  const samples = data.result as PromMatrixSample[];
  return samples.map((sample) => ({
    name: seriesName(sample.metric),
    points: toPoints(sample),
  }));
}

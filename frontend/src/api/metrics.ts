/**
 * 资源指标查询 API 服务(T1.18)。对齐后端 app/api/metrics.py:
 * 控制面屏蔽 Prometheus 直连,前端只经 /api/metrics/query[_range] 取指标。
 *
 * Prometheus 响应 data 形如:
 *   即时(vector): { resultType: "vector", result: [{ metric, value: [ts, "v"] }] }
 *   区间(matrix): { resultType: "matrix", result: [{ metric, values: [[ts, "v"], ...] }] }
 */

import { api } from "./client";

export type PromMetric = Record<string, string>;

export interface PromVectorSample {
  metric: PromMetric;
  value: [number, string];
}

export interface PromMatrixSample {
  metric: PromMetric;
  values: [number, string][];
}

export interface PromVectorData {
  resultType: "vector";
  result: PromVectorSample[];
}

export interface PromMatrixData {
  resultType: "matrix";
  result: PromMatrixSample[];
}

export type PromData = PromVectorData | PromMatrixData | { resultType: string; result: unknown[] };

export interface QueryRangeParams {
  query: string;
  start: number;
  end: number;
  step: number;
}

export function queryInstant(query: string): Promise<PromData> {
  return api.get<PromData>("/api/metrics/query", { params: { query } });
}

export function queryRange(params: QueryRangeParams): Promise<PromData> {
  return api.get<PromData>("/api/metrics/query_range", { params });
}

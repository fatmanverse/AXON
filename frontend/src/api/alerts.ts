/**
 * 告警 API 服务(T3.7)。对齐后端 GET /api/alerts(§6.3/§15.4):
 * 列出告警(可按 status/service 过滤),供主页/告警页展示。入库由 Alertmanager
 * webhook 完成,前端只读。
 */

import { api } from "./client";

export type AlertSeverity = "critical" | "warning" | "info";
export type AlertStatus = "firing" | "resolved";

export interface Alert {
  id: string;
  fingerprint: string;
  service: string | null;
  severity: AlertSeverity;
  summary: string;
  source: string;
  status: AlertStatus;
  fired_at: string | null;
  resolved_at: string | null;
}

export interface ListAlertsParams {
  status?: AlertStatus;
  service?: string;
}

export function listAlerts(params?: ListAlertsParams): Promise<Alert[]> {
  return api.get<Alert[]>("/api/alerts", { params });
}

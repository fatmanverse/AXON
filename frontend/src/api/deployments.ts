/**
 * 部署与配置 API 服务(T2.8)。对齐后端 §12/§15.2:
 * - 部署历史 / 一键回滚(回滚也走 task 异步)。
 * - 配置版本 CRUD:列版本、取当前、新建版本、切换生效版。
 */

import { api } from "./client";
import type { TaskAccepted } from "./services";

export type DeploymentStatus = "running" | "success" | "failed" | "rolled_back";
export type DeploymentSource = "ui-triggered" | "pipeline-webhook" | "manual";
export type DeploymentStrategy = "rolling" | "canary" | "blue-green" | "recreate";
export type ConfigFormat = "env" | "yaml" | "properties" | "json";

export interface Deployment {
  id: string;
  service_id: string;
  env: string;
  git_sha: string | null;
  version: string | null;
  artifact: string | null;
  strategy: DeploymentStrategy;
  source: DeploymentSource;
  pipeline_id: string | null;
  pipeline_url: string | null;
  operator: string | null;
  status: DeploymentStatus;
  previous_deployment_id: string | null;
  scan_result_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ConfigVersion {
  id: string;
  service_id: string;
  version: number;
  content: string;
  format: ConfigFormat;
  created_by: string | null;
  comment: string | null;
  target_path: string | null;
  is_current: boolean;
  created_at: string;
}

export type DeliveryStatus = "pending" | "success" | "failed";

export interface ConfigDelivery {
  id: string;
  config_id: string;
  placement_id: string;
  status: DeliveryStatus;
  result: string | null;
  error: string | null;
  created_at: string;
}

export interface DeployBody {
  version: string;
  strategy?: DeploymentStrategy;
}

export function listDeployments(serviceId: string, env?: string): Promise<Deployment[]> {
  const params = env ? { env } : undefined;
  return api.get<Deployment[]>(`/api/services/${serviceId}/deployments`, { params });
}

export function deployService(serviceId: string, body: DeployBody): Promise<TaskAccepted> {
  return api.post<TaskAccepted>(`/api/services/${serviceId}/deploy`, body);
}

export function rollbackService(serviceId: string): Promise<TaskAccepted> {
  return api.post<TaskAccepted>(`/api/services/${serviceId}/rollback`);
}

export function listConfigVersions(serviceId: string): Promise<ConfigVersion[]> {
  return api.get<ConfigVersion[]>(`/api/services/${serviceId}/configs`);
}

export function getCurrentConfig(serviceId: string): Promise<ConfigVersion | null> {
  return api.get<ConfigVersion | null>(`/api/services/${serviceId}/configs/current`);
}

export function createConfigVersion(
  serviceId: string,
  body: { content: string; format?: ConfigFormat; comment?: string; target_path?: string },
): Promise<ConfigVersion> {
  return api.post<ConfigVersion>(`/api/services/${serviceId}/configs`, body);
}

export function activateConfigVersion(
  serviceId: string,
  version: number,
): Promise<ConfigVersion> {
  return api.post<ConfigVersion>(`/api/services/${serviceId}/configs/${version}/activate`);
}

export function applyConfigVersion(
  serviceId: string,
  version: number,
): Promise<TaskAccepted> {
  return api.post<TaskAccepted>(`/api/services/${serviceId}/configs/${version}/apply`);
}

export function listConfigDeliveries(
  serviceId: string,
  version: number,
): Promise<ConfigDelivery[]> {
  return api.get<ConfigDelivery[]>(
    `/api/services/${serviceId}/configs/${version}/deliveries`,
  );
}

export interface ScanResult {
  id: string;
  service: string;
  git_sha: string;
  scanner: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  passed: boolean;
  report_url: string | null;
}

export interface DeploymentDetail {
  deployment: Deployment;
  scans: ScanResult[];
}

export function getDeploymentDetail(
  serviceId: string,
  deploymentId: string,
): Promise<DeploymentDetail> {
  return api.get<DeploymentDetail>(
    `/api/services/${serviceId}/deployments/${deploymentId}`,
  );
}

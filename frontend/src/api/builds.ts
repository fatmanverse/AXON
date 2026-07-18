/**
 * 构建能力 API 服务(构建能力一期,方案 A「控制面本地构建」)。对齐后端 app/api/builds.py:
 * - 触发构建(异步落 task,前端轮询)/ 构建历史 / 构建详情 / 制品列表。
 * - 制品库 CRUD(docker 库配 url + 凭据,凭据只传明文由后端换保险箱引用)。
 * 构建执行落在控制面本地节点(git clone → 测试 → build → 产出制品),前端只认统一 task 语义。
 */

import { api } from "./client";
import type { TaskAccepted } from "./services";

export type BuildStatus = "pending" | "running" | "success" | "failed" | "canceled";
export type BuildSource = "ui-triggered" | "pipeline-webhook" | "manual";
export type ArtifactRegistryType = "docker" | "generic";

export interface Build {
  id: string;
  service_id: string;
  repo_url: string | null;
  git_ref: string | null;
  git_sha: string | null;
  version: string | null;
  build_node_id: string | null;
  artifact_id: string | null;
  source: BuildSource;
  pipeline_id: string | null;
  pipeline_url: string | null;
  operator: string | null;
  status: BuildStatus;
  log_url: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Artifact {
  id: string;
  registry_id: string;
  service_id: string;
  build_id: string | null;
  git_sha: string | null;
  name: string;
  version: string | null;
  digest: string | null;
  uri: string;
  size_bytes: number | null;
}

export interface ArtifactRegistry {
  id: string;
  name: string;
  type: ArtifactRegistryType;
  url: string;
  credential_id: string | null;
  is_default: boolean;
  description: string;
}

export interface BuildNode {
  id: string;
  name: string;
  server_id: string | null;
  host: string | null;
  ssh_credential_id: string | null;
  status: "online" | "offline" | "unknown";
  labels: Record<string, unknown>;
  max_concurrent: number;
  last_heartbeat_at: string | null;
}

export interface TriggerBuildBody {
  git_ref?: string;
  version?: string;
}

export function triggerBuild(serviceId: string, body: TriggerBuildBody = {}): Promise<TaskAccepted> {
  return api.post<TaskAccepted>(`/api/services/${serviceId}/build`, body);
}

export function listBuilds(serviceId: string, limit?: number): Promise<Build[]> {
  const params = limit ? { limit } : undefined;
  return api.get<Build[]>(`/api/services/${serviceId}/builds`, { params });
}

export function getBuild(buildId: string): Promise<Build> {
  return api.get<Build>(`/api/builds/${buildId}`);
}

export function listArtifacts(serviceId: string, limit?: number): Promise<Artifact[]> {
  const params = limit ? { limit } : undefined;
  return api.get<Artifact[]>(`/api/services/${serviceId}/artifacts`, { params });
}

export function listRegistries(): Promise<ArtifactRegistry[]> {
  return api.get<ArtifactRegistry[]>("/api/artifact-registries");
}

export function createRegistry(body: {
  name: string;
  type: ArtifactRegistryType;
  url?: string;
  credential?: string;
  description?: string;
}): Promise<ArtifactRegistry> {
  return api.post<ArtifactRegistry>("/api/artifact-registries", body);
}

export function deleteRegistry(registryId: string): Promise<{ deleted: boolean }> {
  return api.del<{ deleted: boolean }>(`/api/artifact-registries/${registryId}`);
}

export function listBuildNodes(): Promise<BuildNode[]> {
  return api.get<BuildNode[]>("/api/build-nodes");
}

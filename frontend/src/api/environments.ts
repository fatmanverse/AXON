/**
 * 环境管理 API(需求1)。对齐后端 app/api/environments.py 契约:
 * 自定义环境的增删查。requires_approval 决定该环境的高危操作是否走审批闸门(§10.2)。
 */

import { api } from "./client";

export interface Environment {
  id: string;
  name: string;
  display_name: string | null;
  requires_approval: boolean;
  description: string | null;
}

export interface CreateEnvironmentRequest {
  name: string;
  display_name?: string;
  requires_approval?: boolean;
  description?: string;
}

export function listEnvironments(): Promise<Environment[]> {
  return api.get<Environment[]>("/api/environments");
}

export function createEnvironment(body: CreateEnvironmentRequest): Promise<Environment> {
  return api.post<Environment>("/api/environments", body);
}

export function deleteEnvironment(id: string): Promise<{ deleted: boolean }> {
  return api.del<{ deleted: boolean }>(`/api/environments/${encodeURIComponent(id)}`);
}

/**
 * 服务器纳管 API 服务(T1.16)。对齐后端 app/api/servers.py 契约:
 * - 列表 / 纳管(SSH 存私钥换 credential_id)/ 删除 / 连通性测试。
 * 私钥只在纳管请求里出现一次,响应绝不回传(§13),前端不缓存。
 */

import { api } from "./client";

export type AccessMode = "ssh" | "agent";
export type AgentStatus = "online" | "offline" | "unknown";

export interface Server {
  id: string;
  name: string;
  host: string;
  access_mode: AccessMode;
  ssh_credential_id: string | null;
  agent_id: string | null;
  agent_status: AgentStatus;
  agent_version: string | null;
  labels: Record<string, unknown>;
}

export interface RegisterSshServer {
  name: string;
  host: string;
  access_mode: "ssh";
  username?: string;
  ssh_private_key: string;
  ssh_port?: number;
  labels?: Record<string, unknown>;
}

export interface RegisterAgentServer {
  name: string;
  host: string;
  access_mode: "agent";
  agent_id: string;
  labels?: Record<string, unknown>;
}

export type RegisterServerRequest = RegisterSshServer | RegisterAgentServer;

export interface ConnectivityResult {
  reachable: boolean;
}

export function listServers(): Promise<Server[]> {
  return api.get<Server[]>("/api/servers");
}

export function registerServer(body: RegisterServerRequest): Promise<Server> {
  return api.post<Server>("/api/servers", body);
}

export function deleteServer(serverId: string): Promise<{ deleted: boolean }> {
  return api.del<{ deleted: boolean }>(`/api/servers/${serverId}`);
}

export function testConnection(serverId: string): Promise<ConnectivityResult> {
  return api.post<ConnectivityResult>(`/api/servers/${serverId}/test-connection`);
}

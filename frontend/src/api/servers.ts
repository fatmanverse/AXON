import { api } from "./client";

export type AccessMode = "ssh" | "agent";
export type AgentStatus = "online" | "offline" | "unknown";
export type SshAuthType = "key" | "password";

export interface Server {
  id: string;
  name: string;
  host: string;
  access_mode: AccessMode;
  environment: string | null;
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
  environment: string;
  auth_type: SshAuthType;
  username?: string;
  ssh_private_key?: string;
  ssh_password?: string;
  ssh_port?: number;
  labels?: Record<string, unknown>;
}

export interface RegisterAgentServer {
  name: string;
  host: string;
  access_mode: "agent";
  environment: string;
  agent_id: string;
  labels?: Record<string, unknown>;
}

export type RegisterServerRequest = RegisterSshServer | RegisterAgentServer;

export interface ConnectivityResult {
  reachable: boolean;
}

export interface TaskAccepted {
  task_id: string;
  status: string;
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

export function installAgent(serverId: string): Promise<TaskAccepted> {
  return api.post<TaskAccepted>(`/api/servers/${serverId}/install-agent`);
}

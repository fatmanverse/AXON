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

// 服务器现状(inventory):这台机器上实际跑着什么。各分区独立可用性,
// 探测不到的项 available=false,不拖累其余项。对齐后端 server_inventory 降级语义。
export interface InventorySection {
  available: boolean;
  error: string | null;
}

export interface ContainerInfo {
  name: string;
  image: string;
  status: string;
  state: string;
  ports: string;
}

export interface SystemdServiceInfo {
  unit: string;
  active: string;
  sub: string;
  description: string;
}

export interface ListenPortInfo {
  protocol: string;
  address: string;
  port: string;
  process: string;
}

export interface ResourceSnapshot {
  mem_total_mb: number | null;
  mem_used_mb: number | null;
  disk_total_kb: number | null;
  disk_used_kb: number | null;
  disk_mount: string | null;
  load1: number | null;
  load5: number | null;
  load15: number | null;
}

export interface ServerInventory {
  containers: ContainerInfo[];
  containers_section: InventorySection;
  services: SystemdServiceInfo[];
  services_section: InventorySection;
  ports: ListenPortInfo[];
  ports_section: InventorySection;
  resource: ResourceSnapshot | null;
  resource_section: InventorySection;
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

export function getServerInventory(serverId: string): Promise<ServerInventory> {
  return api.get<ServerInventory>(`/api/servers/${serverId}/inventory`);
}

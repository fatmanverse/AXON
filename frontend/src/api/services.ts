/**
 * 服务与生命周期 API 服务(T1.17)。对齐后端 app/api/services.py 与 tasks.py:
 * - 列表(可按 env/runtime 过滤)/ 创建。
 * - 生命周期 start/stop/restart/delete:均异步落 task,返回 task_id。
 * - 任务进度查询:供操作后轮询回显。
 * 生命周期动作底层多态(systemd/docker/k8s),前端只认统一的 task 语义。
 */

import { api } from "./client";

// env 为任意已创建的自定义环境名(后端 service.env 为字符串,不再是固定枚举)。
export type ServiceEnvironment = string;
export type Runtime = "k8s" | "docker" | "systemd" | "process" | "cloud-fn";
export type ReloadMode = "reload" | "restart";
export type LifecycleAction = "start" | "stop" | "restart" | "delete";

export type TaskStatus = "pending" | "running" | "success" | "failed" | "unknown";

export interface Service {
  id: string;
  name: string;
  env: ServiceEnvironment;
  runtime: Runtime;
  runtime_ref: Record<string, unknown>;
  desired_version: string | null;
  reload_mode: ReloadMode;
  placement_count: number;
}

export interface CreateServiceRequest {
  name: string;
  env: ServiceEnvironment;
  runtime: Runtime;
  runtime_ref: Record<string, unknown>;
  desired_version?: string | null;
}

export interface TaskAccepted {
  task_id: string;
  status: TaskStatus;
}

export interface Task {
  id: string;
  type: string;
  status: TaskStatus;
  target: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface ListServicesParams {
  env?: ServiceEnvironment;
  runtime?: Runtime;
}

export function listServices(params?: ListServicesParams): Promise<Service[]> {
  return api.get<Service[]>("/api/services", { params });
}

export function createService(body: CreateServiceRequest): Promise<Service> {
  return api.post<Service>("/api/services", body);
}

/** start/stop/restart 走 POST /{id}/{action};delete 走 DELETE /{id}。 */
export function runLifecycle(serviceId: string, action: LifecycleAction): Promise<TaskAccepted> {
  if (action === "delete") {
    return api.del<TaskAccepted>(`/api/services/${serviceId}`);
  }
  return api.post<TaskAccepted>(`/api/services/${serviceId}/${action}`);
}

export function getTask(taskId: string): Promise<Task> {
  return api.get<Task>(`/api/tasks/${taskId}`);
}

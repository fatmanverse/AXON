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

export type ArtifactType = "generic" | "docker";
// 供页面表单直接引用(避免各处重复字面量联合)。

/**
 * 服务本地构建配置。对齐后端 BuildConfigModel:按 artifact_type 分形态,
 * generic 需 output_path、docker 需 image_ref。填错 key / 缺形态必填项在
 * 后端提交时(PATCH/POST)当场 422,不再拖到构建后台任务才失败。
 */
export interface BuildConfig {
  repo_url: string;
  build_command: string;
  artifact_type: ArtifactType;
  git_ref: string;
  test_command: string | null;
  version: string | null;
  registry_id: string | null;
  required_labels: Record<string, unknown>;
  output_path: string | null;
  image_ref: string | null;
  dockerfile: string;
}

/** 编写构建配置的入参:默认值(git_ref/dockerfile 等)由后端补齐,前端只需填核心项。 */
export interface BuildConfigInput {
  repo_url: string;
  build_command: string;
  artifact_type: ArtifactType;
  git_ref?: string;
  test_command?: string | null;
  version?: string | null;
  output_path?: string | null;
  image_ref?: string | null;
  dockerfile?: string;
}

export interface Service {
  id: string;
  name: string;
  env: ServiceEnvironment;
  runtime: Runtime;
  runtime_ref: Record<string, unknown>;
  desired_version: string | null;
  reload_mode: ReloadMode;
  placement_count: number;
  build_config: BuildConfig | null;
}

export interface CreateServiceRequest {
  name: string;
  env: ServiceEnvironment;
  runtime: Runtime;
  runtime_ref: Record<string, unknown>;
  desired_version?: string | null;
  build_config?: BuildConfigInput | null;
}

/** 部分更新服务(PATCH):仅提供的字段被更新;build_config 一旦提供整体替换并按形态校验。 */
export interface UpdateServiceRequest {
  desired_version?: string | null;
  build_config?: BuildConfigInput | null;
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

/** 单个服务详情(服务详情页动线主轴):含 placement 计数与 build_config。 */
export function getService(serviceId: string): Promise<Service> {
  return api.get<Service>(`/api/services/${serviceId}`);
}

/** 部分更新服务(PATCH),当前主要承载构建配置编写;build_config 提供即后端校验。 */
export function updateService(serviceId: string, body: UpdateServiceRequest): Promise<Service> {
  return api.patch<Service>(`/api/services/${serviceId}`, body);
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

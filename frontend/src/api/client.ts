/**
 * Axios 客户端封装(T0.7 / T0.11)。
 *
 * 职责:
 * - 统一 baseURL 与超时。
 * - 注入 Bearer 鉴权头(令牌来自 token 模块)。
 * - 解包后端统一 envelope:{success, data, error, meta}。成功返回 data,
 *   失败抛 ApiError,调用方只需 try/catch 或依赖 React Query 的错误态。
 * - 401 统一处理:清理令牌并广播,由路由层跳转登录页。
 */

import axios, {
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import type { Envelope, ErrorBody } from "./types";
import { getToken, setToken } from "./token";

export class ApiError extends Error {
  readonly code: string;
  readonly status?: number;
  readonly details?: unknown;

  constructor(body: ErrorBody, status?: number) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.status = status;
    this.details = body.details;
  }
}

/** 401 时通知外部(路由层订阅以跳转登录),与 token 模块解耦。 */
type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

function attachAuthHeader(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  const token = getToken();
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
}

export function createClient(baseURL = "/"): AxiosInstance {
  const instance = axios.create({
    baseURL,
    timeout: 15_000,
    headers: { "Content-Type": "application/json" },
  });

  instance.interceptors.request.use(attachAuthHeader);

  instance.interceptors.response.use(
    (response) => response,
    (error) => {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        setToken(null);
        onUnauthorized?.();
      }
      return Promise.reject(error);
    },
  );

  return instance;
}

export const http = createClient();

/**
 * 解包 envelope:请求成功且 success=true 时返回 data;
 * 其余情形(HTTP 错误 / success=false)抛 ApiError,附带可用的 code 与 message。
 */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<Envelope<T>>(config);
    const envelope = response.data;
    if (!envelope.success || envelope.error) {
      throw new ApiError(
        envelope.error ?? { code: "unknown", message: "未知错误" },
        response.status,
      );
    }
    return envelope.data as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (axios.isAxiosError(error)) {
      const body = error.response?.data as Envelope<unknown> | undefined;
      if (body?.error) {
        throw new ApiError(body.error, error.response?.status);
      }
      throw new ApiError(
        { code: "network_error", message: error.message || "网络请求失败" },
        error.response?.status,
      );
    }
    throw error;
  }
}

export const api = {
  get: <T>(url: string, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: "GET", url }),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: "POST", url, data }),
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: "PUT", url, data }),
  del: <T>(url: string, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: "DELETE", url }),
};

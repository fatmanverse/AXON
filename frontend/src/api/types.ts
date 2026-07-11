/**
 * 后端统一响应 envelope 的前端镜像(对齐 backend app/core/responses.py)。
 * 所有 REST 响应形如 { success, data, error, meta }。
 */

export interface ErrorBody {
  code: string;
  message: string;
  details?: unknown;
}

export interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: ErrorBody | null;
  meta: Record<string, unknown>;
}

export interface AuthUser {
  username: string;
  roles: string[];
}

export interface LoginResult {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface MeResult {
  username: string;
  roles: string[];
  permissions: string[];
}

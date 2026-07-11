/**
 * 认证 API 服务:登录、查询当前用户。
 * 对齐后端 app/api/auth.py 的契约。
 */

import { api } from "./client";
import type { LoginResult, MeResult } from "./types";

export function login(username: string, password: string): Promise<LoginResult> {
  return api.post<LoginResult>("/api/auth/login", { username, password });
}

export function fetchMe(): Promise<MeResult> {
  return api.get<MeResult>("/api/auth/me");
}

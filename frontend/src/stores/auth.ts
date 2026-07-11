/**
 * 认证状态(zustand)。
 *
 * 服务端状态(user/permissions)与令牌分离:令牌由 api/token 持久化,
 * 此 store 管会话态与登录/登出动作。页面刷新时用持久化令牌拉取 /me 恢复会话。
 */

import { create } from "zustand";

import { fetchMe, login as loginRequest } from "@/api/auth";
import { getToken, setToken } from "@/api/token";
import type { MeResult } from "@/api/types";

interface AuthState {
  user: MeResult | null;
  status: "idle" | "loading" | "authenticated" | "anonymous";
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  restore: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  status: "idle",

  login: async (username, password) => {
    set({ status: "loading" });
    try {
      const result = await loginRequest(username, password);
      setToken(result.access_token);
      const me = await fetchMe();
      set({ user: me, status: "authenticated" });
    } catch (error) {
      setToken(null);
      set({ user: null, status: "anonymous" });
      throw error;
    }
  },

  logout: () => {
    setToken(null);
    set({ user: null, status: "anonymous" });
  },

  restore: async () => {
    if (!getToken()) {
      set({ status: "anonymous" });
      return;
    }
    set({ status: "loading" });
    try {
      const me = await fetchMe();
      set({ user: me, status: "authenticated" });
    } catch {
      setToken(null);
      set({ user: null, status: "anonymous" });
    }
  },

  hasPermission: (permission) => get().user?.permissions.includes(permission) ?? false,
}));

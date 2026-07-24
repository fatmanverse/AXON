/**
 * 认证 store(zustand)。
 * 验证:登录成功落令牌并置 authenticated、登录失败清令牌并抛错、
 * restore 无令牌直接匿名、hasPermission 判定。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores/auth";
import { getToken, setToken } from "@/api/token";
import * as authApi from "@/api/auth";
import { ApiError } from "@/api/client";

const me = {
  username: "admin",
  roles: ["admin"],
  permissions: ["server:prod:delete"],
};

beforeEach(() => {
  useAuthStore.setState({ user: null, status: "idle" });
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useAuthStore.login", () => {
  it("成功后落令牌并进入 authenticated", async () => {
    vi.spyOn(authApi, "login").mockResolvedValue({
      access_token: "jwt-abc",
      token_type: "bearer",
      user: { username: "admin", roles: ["admin"] },
    });
    vi.spyOn(authApi, "fetchMe").mockResolvedValue(me);

    await useAuthStore.getState().login("admin", "pw");

    expect(getToken()).toBe("jwt-abc");
    expect(useAuthStore.getState().status).toBe("authenticated");
    expect(useAuthStore.getState().user?.username).toBe("admin");
  });

  it("失败时清令牌、置匿名并抛出错误", async () => {
    setToken("stale");
    vi.spyOn(authApi, "login").mockRejectedValue(
      new ApiError({ code: "unauthorized", message: "密码错误" }, 401),
    );

    await expect(useAuthStore.getState().login("admin", "bad")).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });
});

describe("useAuthStore.restore", () => {
  it("无令牌时直接匿名,不打后端", async () => {
    const spy = vi.spyOn(authApi, "fetchMe");
    await useAuthStore.getState().restore();
    expect(useAuthStore.getState().status).toBe("anonymous");
    expect(spy).not.toHaveBeenCalled();
  });

  it("有令牌但 /me 失败时清令牌并匿名", async () => {
    setToken("expired");
    vi.spyOn(authApi, "fetchMe").mockRejectedValue(
      new ApiError({ code: "unauthorized", message: "过期" }, 401),
    );
    await useAuthStore.getState().restore();
    expect(getToken()).toBeNull();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });
});

describe("useAuthStore.hasPermission", () => {
  it("按当前用户权限点判定", () => {
    useAuthStore.setState({ user: me, status: "authenticated" });
    expect(useAuthStore.getState().hasPermission("server:prod:delete")).toBe(true);
    expect(useAuthStore.getState().hasPermission("config:prod:write")).toBe(false);
  });
});

/**
 * API 客户端解包与错误处理(T0.7 / T0.11)。
 * 用 axios-mock 验证:envelope 成功解包、success=false 抛 ApiError、
 * HTTP 401 清理令牌并触发未授权回调。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MockAdapter from "axios-mock-adapter";

import { ApiError, api, http, setUnauthorizedHandler } from "@/api/client";
import { getToken, setToken } from "@/api/token";

let mock: MockAdapter;

beforeEach(() => {
  mock = new MockAdapter(http);
});

afterEach(() => {
  mock.restore();
  setUnauthorizedHandler(null);
});

describe("request envelope 解包", () => {
  it("success=true 时返回 data", async () => {
    mock.onGet("/api/ping").reply(200, {
      success: true,
      data: { pong: true },
      error: null,
      meta: {},
    });

    await expect(api.get("/api/ping")).resolves.toEqual({ pong: true });
  });

  it("success=false 时抛 ApiError 并带 code/message", async () => {
    mock.onGet("/api/x").reply(200, {
      success: false,
      data: null,
      error: { code: "bad", message: "坏了" },
      meta: {},
    });

    await expect(api.get("/api/x")).rejects.toMatchObject({
      name: "ApiError",
      code: "bad",
      message: "坏了",
    });
  });

  it("HTTP 错误响应体含 error 时透传业务码", async () => {
    mock.onGet("/api/x").reply(403, {
      success: false,
      data: null,
      error: { code: "forbidden", message: "无权" },
      meta: {},
    });

    await expect(api.get("/api/x")).rejects.toMatchObject({
      code: "forbidden",
      status: 403,
    });
  });

  it("网络错误回退为 network_error", async () => {
    mock.onGet("/api/x").networkError();
    await expect(api.get("/api/x")).rejects.toMatchObject({
      code: "network_error",
    });
  });
});

describe("鉴权头与 401 处理", () => {
  it("有令牌时注入 Authorization 头", async () => {
    setToken("tok123");
    mock.onGet("/api/me").reply((config) => {
      expect(config.headers?.Authorization).toBe("Bearer tok123");
      return [200, { success: true, data: {}, error: null, meta: {} }];
    });
    await api.get("/api/me");
  });

  it("401 时清理令牌并触发未授权回调", async () => {
    setToken("tok123");
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mock.onGet("/api/secure").reply(401, {
      success: false,
      data: null,
      error: { code: "unauthorized", message: "过期" },
      meta: {},
    });

    await expect(api.get("/api/secure")).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBeNull();
    expect(handler).toHaveBeenCalledOnce();
  });
});

/**
 * 配置管理页集成测试。
 * mock /api/services 与 /api/services/{id}/configs 契约,验证:
 * 选中服务后列出配置版本(标记生效中)、新建版本提交内容、切换生效版调 activate。
 * 不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { ConfigsPage } from "@/pages/ConfigsPage";
import { http } from "@/api/client";
import type { Service } from "@/api/services";
import type { ConfigVersion } from "@/api/deployments";

let mock: MockAdapter;

function ok<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ConfigProvider>
        <ConfigsPage />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

const SVC: Service = {
  id: "svc1",
  name: "billing",
  env: "dev",
  runtime: "systemd",
  runtime_ref: { unit_name: "billing.service" },
  desired_version: null,
  reload_mode: "restart",
  placement_count: 1,
};

const CFG_V2: ConfigVersion = {
  id: "c2",
  service_id: "svc1",
  version: 2,
  content: "A=2",
  format: "env",
  created_by: "alice",
  comment: "bump",
  target_path: "/etc/app/app.env",
  is_current: true,
  created_at: "2026-07-11T12:00:00+00:00",
};

const CFG_V1: ConfigVersion = {
  id: "c1",
  service_id: "svc1",
  version: 1,
  content: "A=1",
  format: "env",
  created_by: "alice",
  comment: "init",
  target_path: null,
  is_current: false,
  created_at: "2026-07-11T11:00:00+00:00",
};

beforeEach(() => {
  mock = new MockAdapter(http);
  mock.onGet("/api/services").reply(200, ok([SVC]));
  mock.onGet("/api/services/svc1/configs").reply(200, ok([CFG_V2, CFG_V1]));
  mock.onGet("/api/services/svc1/configs/current").reply(200, ok(CFG_V2));
});

afterEach(() => {
  mock.restore();
});

describe("ConfigsPage", () => {
  it("列出配置版本并标记生效中", async () => {
    renderPage();
    expect(await screen.findByText("生效中")).toBeInTheDocument();
    // 非生效版有"设为生效"按钮
    expect(screen.getByRole("button", { name: "设为生效" })).toBeInTheDocument();
  });

  it("新建配置版本提交内容与格式", async () => {
    mock.onPost("/api/services/svc1/configs").reply((config) => {
      const body = JSON.parse(config.data);
      expect(body.content).toBe("KEY=val");
      return [201, ok({ ...CFG_V2, version: 3, content: "KEY=val" })];
    });

    const user = userEvent.setup();
    renderPage();
    const textarea = await screen.findByPlaceholderText(/A=1/);
    await user.type(textarea, "KEY=val");
    await user.click(screen.getByRole("button", { name: /保存新版本/ }));

    await waitFor(() => {
      expect(mock.history.post.some((r) => r.url === "/api/services/svc1/configs")).toBe(true);
    });
  });

  it("切换生效版调用 activate 端点", async () => {
    mock
      .onPost("/api/services/svc1/configs/1/activate")
      .reply(200, ok({ ...CFG_V1, is_current: true }));

    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "设为生效" }));
    const confirm = await screen.findByRole("button", { name: /^切\s*换$/ });
    await user.click(confirm);

    await waitFor(() => {
      expect(
        mock.history.post.some((r) => r.url === "/api/services/svc1/configs/1/activate"),
      ).toBe(true);
    });
  });

  it("无服务时提示先创建", async () => {
    mock.onGet("/api/services").reply(200, ok([]));
    renderPage();
    expect(await screen.findByText(/先在「服务」页创建/)).toBeInTheDocument();
  });
});

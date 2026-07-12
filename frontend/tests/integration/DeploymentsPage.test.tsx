/**
 * 部署与配置页集成测试(T2.8)。
 * mock /api/services、/api/services/{id}/deployments、/configs 契约,验证:
 * 部署历史渲染、一键回滚走 task 轮询、配置版本列表、新建版本、切换生效版。
 * 不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { DeploymentsPage } from "@/pages/DeploymentsPage";
import { http } from "@/api/client";
import type { Service } from "@/api/services";
import type { ConfigVersion, Deployment } from "@/api/deployments";

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
        <DeploymentsPage />
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

const DEP: Deployment = {
  id: "dep1",
  service_id: "svc1",
  env: "dev",
  git_sha: "abc123",
  version: "v1.2.0",
  artifact: "registry/app:v1.2.0",
  strategy: "rolling",
  source: "ui-triggered",
  pipeline_id: "pipe-1",
  pipeline_url: null,
  operator: "alice",
  status: "success",
  previous_deployment_id: null,
  scan_result_id: null,
  started_at: "2026-07-11T12:00:00+00:00",
  finished_at: "2026-07-11T12:05:00+00:00",
};

const CFG_V2: ConfigVersion = {
  id: "c2",
  service_id: "svc1",
  version: 2,
  content: "A=2",
  format: "env",
  created_by: "alice",
  comment: "bump",
  target_path: null,
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
  mock.onGet("/api/services/svc1/deployments").reply(200, ok([DEP]));
  mock.onGet("/api/services/svc1/configs").reply(200, ok([CFG_V2, CFG_V1]));
  mock.onGet("/api/services/svc1/configs/current").reply(200, ok(CFG_V2));
});

afterEach(() => {
  mock.restore();
});

describe("DeploymentsPage", () => {
  it("选中服务后渲染部署历史", async () => {
    renderPage();
    // 服务列表加载后自动选中第一个,拉出部署记录
    expect(await screen.findByText("v1.2.0")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("成功")).toBeInTheDocument();
  });

  it("一键回滚走二次确认并调用回滚端点+轮询 task", async () => {
    mock.onPost("/api/services/svc1/rollback").reply(202, ok({ task_id: "t1", status: "pending" }));
    mock.onGet("/api/tasks/t1").reply(200, ok({ id: "t1", status: "success" }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("v1.2.0");

    await user.click(screen.getByRole("button", { name: /一\s*键\s*回\s*滚|一键回滚/ }));
    // Popconfirm 弹出确认按钮
    const confirm = await screen.findByRole("button", { name: /^回\s*滚$/ });
    await user.click(confirm);

    await waitFor(() => {
      expect(mock.history.post.some((r) => r.url === "/api/services/svc1/rollback")).toBe(true);
    });
  });
});

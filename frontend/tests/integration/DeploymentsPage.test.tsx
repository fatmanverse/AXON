/**
 * 部署与配置页集成测试(T2.8)。
 * mock /api/services、/api/services/{id}/deployments 契约,验证:
 * 制品展示、历史行定向回滚、审批短路与 task 终态回显。
 * 不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider, message } from "antd";

import { DeploymentsPage } from "@/pages/DeploymentsPage";
import { http } from "@/api/client";
import type { Service } from "@/api/services";
import type { Deployment } from "@/api/deployments";

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

const CURRENT: Deployment = {
  id: "dep-current",
  service_id: "svc1",
  env: "dev",
  git_sha: "abc123",
  version: "v3.0.0",
  artifact: "registry/app:v3.0.0",
  artifact_id: "cccccccccccccccccccccccccccccccc",
  strategy: "rolling",
  source: "ui-triggered",
  pipeline_id: "pipe-1",
  pipeline_url: null,
  operator: "alice",
  status: "success",
  previous_deployment_id: "dep-artifact",
  scan_result_id: null,
  started_at: "2026-07-11T12:00:00+00:00",
  finished_at: "2026-07-11T12:05:00+00:00",
};

const TARGET_ARTIFACT: Deployment = {
  ...CURRENT,
  id: "dep-artifact",
  version: "v2.0.0",
  artifact: "registry/app:v2.0.0",
  artifact_id: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  status: "rolled_back",
  previous_deployment_id: "dep-ci",
  started_at: "2026-07-10T12:00:00+00:00",
};

const TARGET_CI: Deployment = {
  ...CURRENT,
  id: "dep-ci",
  version: "v1.0.0",
  artifact: "registry/app:v1.0.0",
  artifact_id: null,
  status: "success",
  previous_deployment_id: null,
  started_at: "2026-07-09T12:00:00+00:00",
};

beforeEach(() => {
  mock = new MockAdapter(http);
  mock.onGet("/api/services").reply(200, ok([SVC]));
  mock
    .onGet("/api/services/svc1/deployments")
    .reply(200, ok([CURRENT, TARGET_ARTIFACT, TARGET_CI]));
});

afterEach(() => {
  message.destroy();
  mock.restore();
});

describe("DeploymentsPage", () => {
  it("展示 artifact 摘要且仅较早成功历史提供回滚操作", async () => {
    renderPage();
    expect(await screen.findByText("v3.0.0")).toBeInTheDocument();
    expect(screen.getAllByText("alice")).toHaveLength(3);
    expect(screen.getByText("aaaaaaaa…")).toBeInTheDocument();
    expect(screen.getByText("registry/app:v1.0.0")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "一键回滚" })).not.toBeInTheDocument();

    const currentRow = screen.getByText("v3.0.0").closest("tr")!;
    const artifactRow = screen.getByText("v2.0.0").closest("tr")!;
    expect(
      within(currentRow).queryByRole("button", { name: "回滚到此版本" }),
    ).not.toBeInTheDocument();
    expect(within(artifactRow).getByRole("button", { name: "回滚到此版本" })).toBeInTheDocument();
  });

  it("详情展示完整 artifact_id 与 URI", async () => {
    mock
      .onGet("/api/services/svc1/deployments/dep-artifact")
      .reply(200, ok({ deployment: TARGET_ARTIFACT, scans: [] }));
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("v2.0.0")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: "查看详情" }));

    expect(await screen.findByText("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")).toBeInTheDocument();
    expect(screen.getAllByText("registry/app:v2.0.0").length).toBeGreaterThan(0);
  });

  it("确认指定历史版本后提交 target_deployment_id 并轮询成功", async () => {
    mock.onPost("/api/services/svc1/rollback").reply(202, ok({ task_id: "t1", status: "pending" }));
    mock.onGet("/api/tasks/t1").reply(200, ok({ id: "t1", status: "success" }));

    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("v2.0.0")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: "回滚到此版本" }));
    expect(await screen.findByText("确认回滚到此版本")).toBeInTheDocument();
    expect(screen.getAllByText(/billing.*dev/).length).toBeGreaterThan(1);
    expect(screen.getAllByText("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").length).toBeGreaterThan(0);
    const confirm = screen.getByRole("button", { name: "确认回滚" });
    await user.click(confirm);

    await waitFor(() => {
      const request = mock.history.post.find((r) => r.url === "/api/services/svc1/rollback");
      expect(JSON.parse(request?.data as string)).toEqual({
        target_deployment_id: "dep-artifact",
      });
    });
    expect(await screen.findByText("回滚成功")).toBeInTheDocument();
  });

  it("回滚进入审批时不轮询 task", async () => {
    mock
      .onPost("/api/services/svc1/rollback")
      .reply(202, ok({ approval_id: "ap-rb", status: "pending", pending_approval: true }));
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("v1.0.0")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: "回滚到此版本" }));
    await user.click(await screen.findByRole("button", { name: "确认回滚" }));

    expect(await screen.findByText(/已提交审批/)).toBeInTheDocument();
    expect(mock.history.get.some((r) => (r.url ?? "").startsWith("/api/tasks"))).toBe(false);
  });

  it.each([
    ["failed", "回滚失败:runtime down"],
    ["unknown", "回滚状态未知,请稍后核对"],
  ])("回滚 task %s 时显示对应终态", async (status, expected) => {
    mock.onPost("/api/services/svc1/rollback").reply(202, ok({ task_id: "t2", status: "pending" }));
    mock.onGet("/api/tasks/t2").reply(200, ok({ id: "t2", status, error: "runtime down" }));
    const user = userEvent.setup();
    renderPage();
    const row = (await screen.findByText("v1.0.0")).closest("tr")!;

    await user.click(within(row).getByRole("button", { name: "回滚到此版本" }));
    await user.click(await screen.findByRole("button", { name: "确认回滚" }));

    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("prod 部署落 pending 审批时提示已进入审批,不轮询 task", async () => {
    // prod 高危部署返回 approval_id(无 task_id):前端应提示"已提交审批",
    // 绝不能拿 undefined 去轮询 /api/tasks(旧 bug)。
    mock
      .onPost("/api/services/svc1/deploy")
      .reply(200, ok({ approval_id: "ap1", status: "pending", pending_approval: true }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("v3.0.0");

    await user.click(screen.getByRole("button", { name: /触\s*发\s*部\s*署/ }));
    await user.type(await screen.findByPlaceholderText("如 v1.2.3"), "v9.9.9");
    // Modal 底部“部署”确认按钮
    const okBtn = screen.getAllByRole("button", { name: /^部\s*署$/ }).at(-1)!;
    await user.click(okBtn);

    await waitFor(() => {
      expect(mock.history.post.some((r) => r.url === "/api/services/svc1/deploy")).toBe(true);
    });
    // 关键:没有对 /api/tasks 发起任何轮询(pending 审批不轮询)
    expect(mock.history.get.some((r) => (r.url ?? "").startsWith("/api/tasks"))).toBe(false);
    expect(await screen.findByText(/已提交审批/)).toBeInTheDocument();
  });
});

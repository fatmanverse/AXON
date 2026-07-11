/**
 * 服务列表与生命周期操作页集成测试(T1.17)。
 * 用 axios-mock-adapter mock /api/services、生命周期端点与 /api/tasks,验证:
 * 列表渲染、env/runtime 过滤、重启走 task 轮询后回显成功、删除二次确认、边界态。
 * 不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { ServicesPage } from "@/pages/ServicesPage";
import { http } from "@/api/client";
import type { Service } from "@/api/services";

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
        <ServicesPage />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

const SVC: Service = {
  id: "svc1",
  name: "billing",
  env: "prod",
  runtime: "systemd",
  runtime_ref: { unit_name: "billing.service" },
  desired_version: "v1.2.0",
  reload_mode: "restart",
  placement_count: 2,
};

// 忽略中文按钮里 AntD 自动插入的空格(如 "启 动")。
const byName = (name: string) => new RegExp(name.split("").join("\\s*"));

beforeEach(() => {
  mock = new MockAdapter(http);
});

afterEach(() => {
  mock.restore();
});

describe("ServicesPage", () => {
  it("渲染服务列表", async () => {
    mock.onGet("/api/services").reply(200, ok([SVC]));
    renderPage();

    expect(await screen.findByText("billing")).toBeInTheDocument();
    expect(screen.getByText("prod")).toBeInTheDocument();
    expect(screen.getByText("v1.2.0")).toBeInTheDocument();
  });

  it("空列表显示引导文案", async () => {
    mock.onGet("/api/services").reply(200, ok([]));
    renderPage();

    expect(await screen.findByText("暂无服务,点击右上角新建")).toBeInTheDocument();
  });

  it("加载失败显示错误态", async () => {
    mock.onGet("/api/services").reply(500, {
      success: false,
      data: null,
      error: { code: "internal_error", message: "服务器内部错误" },
      meta: {},
    });
    renderPage();

    expect(await screen.findByText("服务器内部错误")).toBeInTheDocument();
  });

  it("重启动作落 task 并轮询回显成功", async () => {
    mock.onGet("/api/services").reply(200, ok([SVC]));
    mock
      .onPost("/api/services/svc1/restart")
      .reply(202, ok({ task_id: "t1", status: "pending" }));
    mock.onGet("/api/tasks/t1").reply(200, ok({
      id: "t1",
      type: "restart",
      status: "success",
      target: "service:svc1",
      result: { action: "restart" },
      error: null,
      created_at: "2026-07-11T12:00:00Z",
      finished_at: "2026-07-11T12:00:01Z",
    }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("billing");

    await user.click(screen.getByRole("button", { name: byName("重启") }));

    await waitFor(() => {
      expect(mock.history.post.some((r) => r.url === "/api/services/svc1/restart")).toBe(
        true,
      );
    });
    expect(await screen.findByText(/重启成功/)).toBeInTheDocument();
  });

  it("删除走二次确认后落 task", async () => {
    mock.onGet("/api/services").reply(200, ok([SVC]));
    mock.onDelete("/api/services/svc1").reply(202, ok({ task_id: "t2", status: "pending" }));
    mock.onGet("/api/tasks/t2").reply(200, ok({
      id: "t2",
      type: "delete",
      status: "success",
      target: "service:svc1",
      result: { action: "delete" },
      error: null,
      created_at: "2026-07-11T12:00:00Z",
      finished_at: "2026-07-11T12:00:01Z",
    }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("billing");

    await user.click(screen.getByRole("button", { name: byName("删除") }));
    // Popconfirm 浮层的确认按钮(取最后一个"删除")
    const deleteButtons = await screen.findAllByRole("button", { name: byName("删除") });
    await user.click(deleteButtons[deleteButtons.length - 1]);

    await waitFor(() => {
      expect(mock.history.delete.some((r) => r.url === "/api/services/svc1")).toBe(true);
    });
  });

  it("按环境过滤时把 env 传给列表接口", async () => {
    mock.onGet("/api/services").reply((config) => {
      // 默认无过滤时返回两条,带 env=dev 时只返回 dev 的
      if (config.params?.env === "dev") {
        return [200, ok([{ ...SVC, id: "svc2", name: "dev-svc", env: "dev" }])];
      }
      return [200, ok([SVC])];
    });

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("billing");

    // AntD Select:聚焦 combobox 后用键盘展开并选中第一项(dev),
    // 比点击 portal 里的下拉项更稳(jsdom 下 placeholder span 有 pointer-events:none)。
    const [envCombobox] = screen.getAllByRole("combobox");
    await user.click(envCombobox);
    const devOption = await screen.findByText("dev", {
      selector: ".ant-select-item-option-content",
    });
    await user.click(devOption);

    expect(await screen.findByText("dev-svc")).toBeInTheDocument();
    await waitFor(() => {
      expect(
        mock.history.get.some((r) => r.url === "/api/services" && r.params?.env === "dev"),
      ).toBe(true);
    });
  });
});

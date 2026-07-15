/**
 * 服务器列表页集成测试(T1.16)。
 * 用 axios-mock-adapter mock /api/servers 契约,验证:列表渲染、空/错误边界态、
 * 纳管表单提交(SSH)、连通性测试、删除。不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { ServersPage } from "@/pages/ServersPage";
import { http } from "@/api/client";
import type { Server } from "@/api/servers";
import type { Environment } from "@/api/environments";

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
        <ServersPage />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

const SSH_SERVER: Server = {
  id: "s1",
  name: "web-01",
  host: "10.0.0.10",
  access_mode: "ssh",
  ssh_credential_id: "cred-1",
  environment: null,
  agent_id: null,
  agent_status: "unknown",
  agent_version: null,
  labels: {},
};

const ENV_PROD: Environment = {
  id: "e1",
  name: "prod",
  display_name: "生产",
  requires_approval: true,
  description: null,
};

beforeEach(() => {
  mock = new MockAdapter(http);
});

afterEach(() => {
  mock.restore();
});

describe("ServersPage", () => {
  it("渲染服务器列表", async () => {
    mock.onGet("/api/servers").reply(200, ok([SSH_SERVER]));
    renderPage();

    expect(await screen.findByText("web-01")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.10")).toBeInTheDocument();
    expect(screen.getByText("SSH")).toBeInTheDocument();
  });

  it("空列表显示引导文案", async () => {
    mock.onGet("/api/servers").reply(200, ok([]));
    renderPage();

    expect(
      await screen.findByText("暂无纳管服务器,点击右上角纳管第一台"),
    ).toBeInTheDocument();
  });

  it("加载失败显示错误态", async () => {
    mock.onGet("/api/servers").reply(500, {
      success: false,
      data: null,
      error: { code: "internal_error", message: "服务器内部错误" },
      meta: {},
    });
    renderPage();

    expect(await screen.findByText("服务器内部错误")).toBeInTheDocument();
  });

  it("纳管 SSH 服务器后刷新列表", async () => {
    mock.onGet("/api/servers").replyOnce(200, ok([]));
    mock.onGet("/api/environments").reply(200, ok([ENV_PROD]));
    mock.onPost("/api/servers").reply(201, ok(SSH_SERVER));
    mock.onGet("/api/servers").reply(200, ok([SSH_SERVER]));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("暂无纳管服务器,点击右上角纳管第一台");

    await user.click(screen.getByRole("button", { name: "纳管服务器" }));
    const drawer = await screen.findByRole("dialog");

    await user.type(within(drawer).getByPlaceholderText("如 web-01"), "web-01");
    await user.type(within(drawer).getByPlaceholderText("如 10.0.0.10"), "10.0.0.10");
    await user.click(within(drawer).getByRole("combobox"));
    await user.click(await screen.findByText("生产 (prod)"));
    await user.type(
      within(drawer).getByPlaceholderText("-----BEGIN OPENSSH PRIVATE KEY-----"),
      "fake-key",
    );
    // AntD 对两字中文按钮会插入空格("纳管"→"纳 管"),用正则忽略空格匹配
    await user.click(within(drawer).getByRole("button", { name: /纳\s*管/ }));

    // 提交后请求体应含私钥且模式为 ssh
    await waitFor(() => {
      const posted = mock.history.post.find((r) => r.url === "/api/servers");
      expect(posted).toBeTruthy();
      const body = JSON.parse(posted!.data);
      expect(body.access_mode).toBe("ssh");
      expect(body.ssh_private_key).toBe("fake-key");
      expect(body.name).toBe("web-01");
    });
  });

  it("连通性测试调用对应端点", async () => {
    mock.onGet("/api/servers").reply(200, ok([SSH_SERVER]));
    mock.onPost("/api/servers/s1/test-connection").reply(200, ok({ reachable: true }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("web-01");

    await user.click(screen.getByRole("button", { name: "连通性测试" }));

    await waitFor(() => {
      expect(
        mock.history.post.some((r) => r.url === "/api/servers/s1/test-connection"),
      ).toBe(true);
    });
  });

  it("删除服务器走二次确认后调用删除端点", async () => {
    mock.onGet("/api/servers").reply(200, ok([SSH_SERVER]));
    mock.onDelete("/api/servers/s1").reply(200, ok({ deleted: true }));

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("web-01");

    // AntD 两字按钮会插入空格("删除"→"删 除"),用正则忽略空格匹配。
    // 先点行内删除弹出 Popconfirm,再点浮层里的确认(最后一个"删除")。
    await user.click(screen.getByRole("button", { name: /删\s*除/ }));
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /删\s*除/ }).length).toBeGreaterThan(1);
    });
    const deleteButtons = screen.getAllByRole("button", { name: /删\s*除/ });
    await user.click(deleteButtons[deleteButtons.length - 1]);

    await waitFor(() => {
      expect(mock.history.delete.some((r) => r.url === "/api/servers/s1")).toBe(true);
    });
  });
});

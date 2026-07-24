/**
 * 告警页集成测试(T3.7)。mock /api/alerts 契约,验证:
 * 告警列表渲染、按状态过滤、严重级别中文标签、空态。不触真实后端。
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { AlertsPage } from "@/pages/AlertsPage";
import { http } from "@/api/client";
import type { Alert } from "@/api/alerts";

let mock: MockAdapter;

function ok<T>(data: T) {
  return { success: true, data, error: null, meta: {} };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ConfigProvider>
        <AlertsPage />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

const FIRING: Alert = {
  id: "a1",
  fingerprint: "fp1",
  service: "billing",
  severity: "critical",
  summary: "CPU 飙高",
  source: "alertmanager",
  status: "firing",
  fired_at: "2026-07-11T12:00:00+00:00",
  resolved_at: null,
};

const RESOLVED: Alert = {
  id: "a2",
  fingerprint: "fp2",
  service: "orders",
  severity: "info",
  summary: "已恢复",
  source: "alertmanager",
  status: "resolved",
  fired_at: "2026-07-11T10:00:00+00:00",
  resolved_at: "2026-07-11T11:00:00+00:00",
};

beforeEach(() => {
  mock = new MockAdapter(http);
});

afterEach(() => {
  mock.restore();
});

describe("AlertsPage", () => {
  it("渲染告警列表", async () => {
    mock.onGet("/api/alerts").reply(200, ok([FIRING, RESOLVED]));
    renderPage();
    expect(await screen.findByText("CPU 飙高")).toBeInTheDocument();
    expect(screen.getAllByText("已恢复").length).toBeGreaterThan(0);
  });

  it("严重级别用中文标签", async () => {
    mock.onGet("/api/alerts").reply(200, ok([FIRING]));
    renderPage();
    await screen.findByText("CPU 飙高");
    expect(screen.getAllByText("严重").length).toBeGreaterThan(0);
  });

  it("按状态过滤 firing 时把参数传给接口", async () => {
    mock.onGet("/api/alerts").reply((config) => {
      if (config.params?.status === "firing") {
        return [200, ok([FIRING])];
      }
      return [200, ok([FIRING, RESOLVED])];
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("CPU 飙高");

    await user.click(screen.getByText("触发中", { selector: ".ant-segmented-item-label" }));

    await waitFor(() => {
      expect(mock.history.get.some((r) => r.params?.status === "firing")).toBe(true);
    });
  });

  it("空态显示无告警", async () => {
    mock.onGet("/api/alerts").reply(200, ok([]));
    renderPage();
    expect(await screen.findByText(/暂无告警/)).toBeInTheDocument();
  });
});

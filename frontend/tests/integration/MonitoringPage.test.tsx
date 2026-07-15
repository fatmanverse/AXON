/**
 * 资源监控大盘页集成测试(T1.18)。
 * mock echarts-for-react(jsdom 无 canvas),聚焦数据流:无服务器空态、
 * 有服务器时按 instance=host:9100 拼 PromQL 调 query_range、切服务器重查。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

import { http } from "@/api/client";
import type { Server } from "@/api/servers";

// ECharts 依赖 canvas,jsdom 不支持;桩成一个只暴露关键 option 的占位。
vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => {
    const seriesCount = Array.isArray((option as { series?: unknown[] }).series)
      ? (option as { series: unknown[] }).series.length
      : 0;
    return <div data-testid="echart" data-series={seriesCount} />;
  },
}));

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
        <MonitoringPageWrapper />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

// 延迟 import 让 vi.mock 先生效
import { MonitoringPage } from "@/pages/MonitoringPage";
function MonitoringPageWrapper() {
  return <MonitoringPage />;
}

const SERVER_A: Server = {
  id: "sa",
  name: "web-01",
  host: "10.0.0.10",
  access_mode: "ssh",
  environment: null,
  ssh_credential_id: "c1",
  agent_id: null,
  agent_status: "unknown",
  agent_version: null,
  labels: {},
};

const SERVER_B: Server = {
  ...SERVER_A,
  id: "sb",
  name: "web-02",
  host: "10.0.0.11",
};

const MATRIX = {
  resultType: "matrix",
  result: [
    { metric: { instance: "10.0.0.10:9100" }, values: [[1720000000, "12.5"], [1720000060, "13.0"]] },
  ],
};

beforeEach(() => {
  mock = new MockAdapter(http);
  // 部署标注的服务下拉默认无服务(本文件聚焦服务器指标流,不测标注)
  mock.onGet("/api/services").reply(200, ok([]));
});

afterEach(() => {
  mock.restore();
});

describe("MonitoringPage", () => {
  it("无纳管服务器显示引导空态", async () => {
    mock.onGet("/api/servers").reply(200, ok([]));
    renderPage();

    expect(
      await screen.findByText(
        "暂无纳管服务器,先在「服务器」页纳管并自举 node_exporter",
      ),
    ).toBeInTheDocument();
  });

  it("有服务器时按 instance 拼 PromQL 调 query_range 画四张卡", async () => {
    mock.onGet("/api/servers").reply(200, ok([SERVER_A]));
    mock.onGet("/api/metrics/query_range").reply(200, ok(MATRIX));

    renderPage();

    await waitFor(() => {
      const rangeCalls = mock.history.get.filter(
        (r) => r.url === "/api/metrics/query_range",
      );
      // 四张资源卡各发一次区间查询
      expect(rangeCalls.length).toBe(4);
    });

    // PromQL 应带选中机的 instance=host:9100
    const rangeCalls = mock.history.get.filter(
      (r) => r.url === "/api/metrics/query_range",
    );
    expect(
      rangeCalls.every((r) =>
        String(r.params?.query).includes('instance="10.0.0.10:9100"'),
      ),
    ).toBe(true);

    // 四张图表卡都渲染
    expect(await screen.findAllByTestId("echart")).toHaveLength(4);
  });

  it("切换服务器后按新 instance 重查", async () => {
    mock.onGet("/api/servers").reply(200, ok([SERVER_A, SERVER_B]));
    mock.onGet("/api/metrics/query_range").reply(200, ok(MATRIX));

    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(
        mock.history.get.filter((r) => r.url === "/api/metrics/query_range").length,
      ).toBe(4);
    });
    mock.resetHistory();

    // 打开服务器下拉切到 web-02(第一个下拉是服务器,第二个是部署标注服务选择)
    const combobox = screen.getAllByRole("combobox")[0];
    await user.click(combobox);
    const option = await screen.findByText("web-02（10.0.0.11）", {
      selector: ".ant-select-item-option-content",
    });
    await user.click(option);

    await waitFor(() => {
      const calls = mock.history.get.filter(
        (r) => r.url === "/api/metrics/query_range",
      );
      expect(calls.length).toBeGreaterThan(0);
      expect(
        calls.every((r) =>
          String(r.params?.query).includes('instance="10.0.0.11:9100"'),
        ),
      ).toBe(true);
    });
  });
});

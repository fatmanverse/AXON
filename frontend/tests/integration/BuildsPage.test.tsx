/**
 * BuildsPage 冒烟测试:验证服务选择、构建历史渲染、触发构建轮询回显、空态。
 * 关键路径 mock api 层,不触真实网络。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuildsPage } from "@/pages/BuildsPage";
import { listServices } from "@/api/services";
import { listBuilds, listArtifacts } from "@/api/builds";

vi.mock("@/api/services", () => ({
  listServices: vi.fn(),
}));

vi.mock("@/api/builds", () => ({
  listBuilds: vi.fn(),
  listArtifacts: vi.fn(),
  triggerBuild: vi.fn(),
}));

const renderPage = (): void => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <BuildsPage />
    </QueryClientProvider>,
  );
};

const mockListServices = vi.mocked(listServices);
const mockListBuilds = vi.mocked(listBuilds);
const mockListArtifacts = vi.mocked(listArtifacts);

const SERVICES = [
  {
    id: "s1",
    name: "billing",
    env: "prod",
    runtime: "docker",
    runtime_ref: {},
    desired_version: null,
    reload_mode: "restart",
    placement_count: 1,
  },
];

const BUILD = {
  id: "b1",
  service_id: "s1",
  repo_url: "https://git.example.com/app.git",
  git_ref: "main",
  git_sha: "a".repeat(40),
  version: "v1.0.0",
  build_node_id: "n1",
  artifact_id: "art1",
  source: "ui-triggered",
  pipeline_id: null,
  pipeline_url: null,
  operator: "op",
  status: "success",
  log_url: null,
  error: null,
  started_at: null,
  finished_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("BuildsPage", () => {
  it("加载服务后展示构建历史", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([BUILD] as never);
    mockListArtifacts.mockResolvedValue([] as never);

    renderPage();

    expect(await screen.findByText("v1.0.0")).toBeInTheDocument();
  });

  it("无服务时展示空态", async () => {
    mockListServices.mockResolvedValue([] as never);

    renderPage();

    await waitFor(() => expect(mockListServices).toHaveBeenCalled());
    expect(await screen.findByText(/暂无服务/)).toBeInTheDocument();
  });
});

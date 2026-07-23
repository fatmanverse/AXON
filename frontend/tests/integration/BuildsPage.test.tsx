/**
 * BuildsPage 冒烟测试:验证服务选择、构建历史渲染、触发构建轮询回显、空态。
 * 关键路径 mock api 层,不触真实网络。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BuildsPage } from "@/pages/BuildsPage";
import { listServices } from "@/api/services";
import { listBuilds, listArtifacts } from "@/api/builds";
import { deployService, isPendingApproval } from "@/api/deployments";
import { pollTaskUntilDone } from "@/api/taskPolling";

vi.mock("@/api/services", () => ({
  listServices: vi.fn(),
}));

vi.mock("@/api/builds", () => ({
  listBuilds: vi.fn(),
  listArtifacts: vi.fn(),
  triggerBuild: vi.fn(),
}));

vi.mock("@/api/deployments", () => ({
  deployService: vi.fn(),
  isPendingApproval: vi.fn(() => false),
}));

// pollTaskUntilDone 在测试中直接 resolve
vi.mock("@/api/taskPolling", () => ({
  pollTaskUntilDone: vi.fn().mockResolvedValue({ status: "success" }),
}));

const renderPage = (): QueryClient => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <BuildsPage />
    </QueryClientProvider>,
  );
  return queryClient;
};

const mockListServices = vi.mocked(listServices);
const mockListBuilds = vi.mocked(listBuilds);
const mockListArtifacts = vi.mocked(listArtifacts);
const mockDeployService = vi.mocked(deployService);
const mockIsPendingApproval = vi.mocked(isPendingApproval);
const mockPollTaskUntilDone = vi.mocked(pollTaskUntilDone);

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

const ARTIFACT = {
  id: "art1",
  service_id: "s1",
  registry_id: "reg1",
  build_id: "b1",
  git_sha: "a".repeat(40),
  name: "app",
  version: "v1.0.0",
  digest: null,
  uri: "/var/lib/axon/artifacts/app.tar.gz",
  size_bytes: 1024,
  meta: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  mockIsPendingApproval.mockReturnValue(false);
  mockPollTaskUntilDone.mockResolvedValue({ status: "success" } as never);
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

  it("构建产物 tab 展示部署按钮并确认 artifact 请求", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    mockDeployService.mockResolvedValue({ task_id: "task1", status: "pending" } as never);

    const queryClient = renderPage();
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");

    // 等待服务加载,切换到构建产物 tab(Segmented 的 radio input,pointer-events:none
    // 故用 fireEvent 绕过指针校验)
    await screen.findByText("构建历史");
    fireEvent.click(screen.getByRole("radio", { name: "构建产物" }));

    const deployButton = await screen.findByRole("button", { name: "部署制品 app" });
    fireEvent.click(deployButton);
    expect((await screen.findAllByText("部署制品")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("/var/lib/axon/artifacts/app.tar.gz").length).toBeGreaterThan(1);

    fireEvent.click(screen.getByRole("button", { name: /确\s*认\s*部\s*署/ }));
    await waitFor(() => {
      expect(mockDeployService).toHaveBeenCalledWith("s1", {
        artifact_id: "art1",
        strategy: "rolling",
      });
    });
    expect(mockPollTaskUntilDone).toHaveBeenCalledWith("task1", { timeoutMs: 60_000 });
    await waitFor(() => {
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["artifacts", "s1"],
      });
      expect(invalidateQueries).toHaveBeenCalledWith({
        queryKey: ["deployments", "s1"],
      });
    });
  });

  it("pending approval 不轮询 task", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    mockDeployService.mockResolvedValue({
      approval_id: "approval1",
      status: "pending",
      pending_approval: true,
    } as never);
    mockIsPendingApproval.mockReturnValue(true);

    renderPage();
    await screen.findByText("构建历史");
    fireEvent.click(screen.getByRole("radio", { name: "构建产物" }));
    fireEvent.click(await screen.findByRole("button", { name: "部署制品 app" }));
    fireEvent.click(await screen.findByRole("button", { name: /确\s*认\s*部\s*署/ }));

    await waitFor(() => expect(mockDeployService).toHaveBeenCalled());
    expect(mockPollTaskUntilDone).not.toHaveBeenCalled();
  });

  it("task 失败时显示失败提示而不是成功", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    mockDeployService.mockResolvedValue({ task_id: "task1", status: "pending" } as never);
    mockPollTaskUntilDone.mockResolvedValue({
      status: "failed",
      error: "runtime unavailable",
    } as never);

    renderPage();
    await screen.findByText("构建历史");
    fireEvent.click(screen.getByRole("radio", { name: "构建产物" }));
    fireEvent.click(await screen.findByRole("button", { name: "部署制品 app" }));
    fireEvent.click(await screen.findByRole("button", { name: /确\s*认\s*部\s*署/ }));

    await waitFor(() => expect(mockPollTaskUntilDone).toHaveBeenCalled());
    expect(await screen.findByText("制品部署失败:runtime unavailable")).toBeInTheDocument();
  });
});

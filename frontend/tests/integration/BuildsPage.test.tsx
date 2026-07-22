/**
 * BuildsPage 冒烟测试:验证服务选择、构建历史渲染、触发构建轮询回显、空态。
 * 关键路径 mock api 层,不触真实网络。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Modal } from "antd";

import { BuildsPage } from "@/pages/BuildsPage";
import { listServices } from "@/api/services";
import { listBuilds, listArtifacts } from "@/api/builds";
import { deployService } from "@/api/deployments";
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
  isPendingApproval: (result: { pending_approval?: boolean }) =>
    result.pending_approval === true,
}));

vi.mock("@/api/taskPolling", () => ({
  pollTaskUntilDone: vi.fn(),
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
const mockDeployService = vi.mocked(deployService);
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
  registry_id: "reg1",
  service_id: "s1",
  build_id: "b1",
  git_sha: "a".repeat(40),
  name: "billing",
  version: "v1.0.0",
  digest: "sha256:" + "b".repeat(64),
  uri: "registry.example.com/team/billing:v1.0.0",
  size_bytes: 1024,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockDeployService.mockResolvedValue({ task_id: "t1", status: "pending" });
  mockPollTaskUntilDone.mockResolvedValue({
    id: "t1",
    type: "deploy",
    status: "success",
    target: "service:s1",
    result: { version: "v1.0.0" },
    error: null,
    created_at: "2026-07-22T00:00:00Z",
    finished_at: "2026-07-22T00:00:01Z",
  });
});

afterEach(() => {
  Modal.destroyAll();
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

  it("从 artifact 行确认部署并在成功后刷新", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([BUILD] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText("构建产物"));
    await user.click(await screen.findByRole("button", { name: "部署" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText("确认部署构建产物").length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText("billing(prod)").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("docker")).toBeInTheDocument();
    expect(within(dialog).getByText(ARTIFACT.uri)).toBeInTheDocument();

    const confirm = within(dialog).getByRole("button", { name: /部\s*署/ });
    await user.click(confirm);

    await waitFor(() => {
      expect(mockDeployService).toHaveBeenCalledWith("s1", {
        artifact_id: "art1",
        strategy: "rolling",
      });
    });
    expect(mockPollTaskUntilDone).toHaveBeenCalledWith("t1");
    await waitFor(() => expect(mockListArtifacts.mock.calls.length).toBeGreaterThan(1));
    expect(await screen.findByText("制品部署成功")).toBeInTheDocument();
  });

  it("artifact 部署进入审批时不轮询 task", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([BUILD] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    mockDeployService.mockResolvedValue({
      approval_id: "ap1",
      status: "pending",
      pending_approval: true,
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText("构建产物"));
    await user.click(await screen.findByRole("button", { name: "部署" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /部\s*署/ }),
    );

    expect(await screen.findByText(/已提交审批/)).toBeInTheDocument();
    expect(mockPollTaskUntilDone).not.toHaveBeenCalled();
  });

  it("artifact 部署失败时展示 task 错误", async () => {
    mockListServices.mockResolvedValue(SERVICES as never);
    mockListBuilds.mockResolvedValue([BUILD] as never);
    mockListArtifacts.mockResolvedValue([ARTIFACT] as never);
    mockPollTaskUntilDone.mockResolvedValue({
      id: "t1",
      type: "deploy",
      status: "failed",
      target: "service:s1",
      result: null,
      error: "镜像拉取失败",
      created_at: "2026-07-22T00:00:00Z",
      finished_at: "2026-07-22T00:00:01Z",
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByText("构建产物"));
    await user.click(await screen.findByRole("button", { name: "部署" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: /部\s*署/ }),
    );

    expect(await screen.findByText(/制品部署失败:镜像拉取失败/)).toBeInTheDocument();
  });
});

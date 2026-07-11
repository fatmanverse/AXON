/**
 * task 轮询工具单测(T1.17)。用假 fetcher 与 fake timers 验证:
 * - 已终态一次即返回,不重复拉取。
 * - running → success 会持续轮询到终态。
 * - 超时后返回最后状态(仍 running),不抛。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isTerminal, pollTaskUntilDone } from "@/api/taskPolling";
import type { Task, TaskStatus } from "@/api/services";

function task(status: TaskStatus): Task {
  return {
    id: "t1",
    type: "restart",
    status,
    target: "service:s1",
    result: null,
    error: null,
    created_at: "2026-07-11T12:00:00Z",
    finished_at: null,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("isTerminal", () => {
  it("success/failed/unknown 为终态,pending/running 非终态", () => {
    expect(isTerminal("success")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("unknown")).toBe(true);
    expect(isTerminal("pending")).toBe(false);
    expect(isTerminal("running")).toBe(false);
  });
});

describe("pollTaskUntilDone", () => {
  it("已是终态时一次返回,不再轮询", async () => {
    const fetcher = vi.fn().mockResolvedValue(task("success"));

    const result = await pollTaskUntilDone("t1", { fetcher });

    expect(result.status).toBe("success");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("running 持续轮询直到 success", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(task("running"))
      .mockResolvedValueOnce(task("running"))
      .mockResolvedValueOnce(task("success"));

    const promise = pollTaskUntilDone("t1", { intervalMs: 1000, fetcher });
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result.status).toBe("success");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("超时后返回最后一次状态,不抛", async () => {
    const fetcher = vi.fn().mockResolvedValue(task("running"));

    const promise = pollTaskUntilDone("t1", {
      intervalMs: 1000,
      timeoutMs: 2500,
      fetcher,
    });
    await vi.runAllTimersAsync();
    const result = await promise;

    expect(result.status).toBe("running");
  });
});

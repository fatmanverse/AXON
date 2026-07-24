/**
 * 轮询一个任务直到终态(T1.17)。生命周期动作异步落 task,前端据 task_id
 * 轮询进度并回显(推送为主、轮询兜底,§2)。
 *
 * 终态为 success/failed/unknown(unknown 是超时/断连待核对态,§5.4);
 * pending/running 继续轮询,超时后返回当前(可能仍是 running)状态。
 */

import { getTask, type Task, type TaskStatus } from "./services";

const TERMINAL: ReadonlySet<TaskStatus> = new Set<TaskStatus>(["success", "failed", "unknown"]);

export function isTerminal(status: TaskStatus): boolean {
  return TERMINAL.has(status);
}

export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  /** 便于测试注入假 getTask;默认走真实 API。 */
  fetcher?: (taskId: string) => Promise<Task>;
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * 轮询直到 task 到终态或超时。超时返回最后一次拿到的 task(不抛),
 * 由调用方按状态决定提示文案。
 */
export async function pollTaskUntilDone(
  taskId: string,
  { intervalMs = 1000, timeoutMs = 30_000, fetcher = getTask }: PollOptions = {},
): Promise<Task> {
  const deadline = Date.now() + timeoutMs;
  let task = await fetcher(taskId);
  while (!isTerminal(task.status) && Date.now() < deadline) {
    await delay(intervalMs);
    task = await fetcher(taskId);
  }
  return task;
}

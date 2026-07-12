/**
 * 审批 API 服务(T2.15,§10.2/§13)。对齐后端 /api/approvals:
 * prod 高危操作(deploy)开启审批时先落 pending,审批人 approve/reject 决策。
 * 前端审批面板列待审批并驱动决策(四眼原则:发起人不能批准自己的操作)。
 */

import { api } from "./client";

export type ApprovalStatus = "pending" | "approved" | "rejected";
export type ApprovalAction = "deploy" | "delete" | "rollback";

export interface Approval {
  id: string;
  service_id: string;
  env: string;
  action: ApprovalAction;
  status: ApprovalStatus;
  requested_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  task_id: string | null;
  reason: string | null;
  created_at: string;
}

export function listApprovals(env?: string): Promise<Approval[]> {
  return api.get<Approval[]>("/api/approvals", { params: env ? { env } : undefined });
}

export function approveApproval(approvalId: string): Promise<{ approval_id: string; task_id: string; status: string }> {
  return api.post(`/api/approvals/${approvalId}/approve`);
}

export function rejectApproval(approvalId: string, reason?: string): Promise<Approval> {
  return api.post<Approval>(`/api/approvals/${approvalId}/reject`, { reason });
}

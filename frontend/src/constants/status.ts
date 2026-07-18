/**
 * 状态 → 展示元数据(颜色 + 中文标签)的单一事实源。
 *
 * 此前各页面各写一套 `Record<Status, {color,label}>`,同一状态在主页与详情页
 * 色值/文案不一致(如 rolled_back 有的用 #8C8C8C 有的用 neutral、info 有的
 * `default` 有的十六进制)。集中到此处后,主页告警区与告警页、部署 feed 与部署页
 * 复用同一份定义,视觉与文案严格一致。
 *
 * 颜色一律走 @/theme 的 colors,不在此硬编码十六进制(theme 是色板权威)。
 * 值送入 AntD `<Tag color={...}>`:接受十六进制或语义色名("default")。
 */

import type { AlertSeverity, AlertStatus } from "@/api/alerts";
import type { ApprovalStatus } from "@/api/approvals";
import type { BuildStatus } from "@/api/builds";
import type { DeploymentStatus, DeliveryStatus } from "@/api/deployments";
import type { AgentStatus } from "@/api/servers";
import { colors } from "@/theme";

/** 状态展示元数据:Tag 颜色(十六进制或 AntD 语义色名)+ 中文标签。 */
export interface StatusMeta {
  color: string;
  label: string;
}

/** 部署状态(部署 feed / 部署页 / 监控标注共用)。 */
export const DEPLOYMENT_STATUS: Record<DeploymentStatus, StatusMeta> = {
  running: { color: colors.warning, label: "部署中" },
  success: { color: colors.success, label: "成功" },
  failed: { color: colors.danger, label: "失败" },
  rolled_back: { color: colors.neutral, label: "已回滚" },
};

/** 构建状态(构建页 / 构建历史共用)。canceled 归中性(主动取消非失败)。 */
export const BUILD_STATUS: Record<BuildStatus, StatusMeta> = {
  pending: { color: colors.neutral, label: "排队中" },
  running: { color: colors.warning, label: "构建中" },
  success: { color: colors.success, label: "成功" },
  failed: { color: colors.danger, label: "失败" },
  canceled: { color: colors.neutral, label: "已取消" },
};

/** 告警级别(主页告警区 / 告警页共用)。 */
export const ALERT_SEVERITY: Record<AlertSeverity, StatusMeta> = {
  critical: { color: colors.danger, label: "严重" },
  warning: { color: colors.warning, label: "警告" },
  info: { color: colors.neutral, label: "提示" },
};

/** 告警状态(触发中 / 已恢复)。 */
export const ALERT_STATUS: Record<AlertStatus, StatusMeta> = {
  firing: { color: colors.danger, label: "触发中" },
  resolved: { color: colors.success, label: "已恢复" },
};

/** 审批状态(待审批 / 已通过 / 已驳回)。 */
export const APPROVAL_STATUS: Record<ApprovalStatus, StatusMeta> = {
  pending: { color: colors.warning, label: "待审批" },
  approved: { color: colors.success, label: "已通过" },
  rejected: { color: colors.danger, label: "已驳回" },
};

/** Agent 在线状态(仅 agent 接入模式的服务器有意义)。 */
export const AGENT_STATUS: Record<AgentStatus, StatusMeta> = {
  online: { color: colors.success, label: "在线" },
  offline: { color: colors.danger, label: "离线" },
  unknown: { color: "default", label: "未知" },
};

/** 配置逐目标下发状态。 */
export const CONFIG_DELIVERY_STATUS: Record<DeliveryStatus, StatusMeta> = {
  success: { color: colors.success, label: "成功" },
  failed: { color: colors.danger, label: "失败" },
  pending: { color: colors.neutral, label: "待下发" },
};

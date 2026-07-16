/**
 * 审批面板页(T2.15,设计 §10.2/§13)。
 *
 * 列出 prod 高危操作的待审批(pending),审批人可批准或拒绝:
 * - 批准:走后端与直接部署一致的编排路径,落 task 异步执行。
 * - 拒绝:关闭审批并记录理由,不执行任何动作。
 * 四眼原则由后端强制(不能批准自己发起的操作),前端只呈现结果。
 */

import { useState } from "react";
import { Button, Card, Input, Modal, Popconfirm, Result, Skeleton, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  type Approval,
  approveApproval,
  listApprovals,
  rejectApproval,
} from "@/api/approvals";
import { Muted } from "@/components/Muted";
import { PageHeader } from "@/components/PageHeader";
import { colors, shadows } from "@/theme";

const ENV_TAG: Record<string, string> = {
  dev: colors.success,
  staging: colors.warning,
  prod: colors.danger,
};

export function ApprovalsPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => listApprovals(),
  });

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["approvals"] });
  };

  const approveMutation = useMutation({
    mutationFn: (id: string) => approveApproval(id),
    onSuccess: () => {
      message.success("已批准,部署已提交执行");
      refresh();
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "批准失败");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason: r }: { id: string; reason: string }) =>
      rejectApproval(id, r || undefined),
    onSuccess: () => {
      message.success("已拒绝");
      setRejectId(null);
      setReason("");
      refresh();
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "拒绝失败");
    },
  });

  const columns: ColumnsType<Approval> = [
    { title: "动作", dataIndex: "action", key: "action", width: 90 },
    {
      title: "环境",
      dataIndex: "env",
      key: "env",
      width: 90,
      render: (e: string) => <Tag color={ENV_TAG[e] ?? "default"}>{e}</Tag>,
    },
    {
      title: "发起人",
      dataIndex: "requested_by",
      key: "requested_by",
      render: (v: string | null) => v ?? <Muted />,
    },
    {
      title: "发起时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 180,
      render: (t: string) => new Date(t).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      key: "ops",
      width: 180,
      render: (_, row) => (
        <div style={{ display: "flex", gap: 8 }}>
          <Popconfirm
            title="确认批准这次生产操作?"
            description="批准后将立即执行部署。"
            okText="批准"
            cancelText="取消"
            onConfirm={() => approveMutation.mutate(row.id)}
          >
            <Button size="small" type="primary" loading={approveMutation.isPending}>
              批准
            </Button>
          </Popconfirm>
          <Button size="small" danger onClick={() => setRejectId(row.id)}>
            拒绝
          </Button>
        </div>
      ),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载待审批失败"}
      />
    );
  }

  return (
    <div>
      <PageHeader title="待审批(生产高危操作)" />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Approval>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无待审批" }}
          />
        </Card>
      )}
      <Modal
        title="拒绝审批"
        open={rejectId !== null}
        onCancel={() => {
          setRejectId(null);
          setReason("");
        }}
        onOk={() => {
          if (rejectId) rejectMutation.mutate({ id: rejectId, reason });
        }}
        okText="确认拒绝"
        okButtonProps={{ danger: true, loading: rejectMutation.isPending }}
        cancelText="取消"
      >
        <Input.TextArea
          rows={3}
          placeholder="拒绝理由(可选)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Modal>
    </div>
  );
}

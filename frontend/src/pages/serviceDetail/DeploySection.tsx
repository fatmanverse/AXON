/**
 * 服务详情页「部署」分区(块三:部署动线收敛)。
 *
 * 把此前「部署」页的部署历史 + 回滚,与「构建」页产物 Tab 的制品部署,收敛到
 * 一处:顶部「部署」按钮打开统一部署入口(DeployServiceModal,制品/版本二选一),
 * 下方是部署历史表,对历史成功版一键回滚。制品不再单独一个 Tab——部署来源在
 * 部署弹窗里选,消除两套部署逻辑。
 */

import { useState } from "react";
import { Button, Card, Descriptions, Result, Skeleton, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Modal } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  type Deployment,
  type DeploymentStatus,
  isPendingApproval,
  listDeployments,
  rollbackService,
} from "@/api/deployments";
import type { Service } from "@/api/services";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { DeployServiceModal } from "@/components/DeployServiceModal";
import { Muted } from "@/components/Muted";
import { TableToolbar } from "@/components/TableToolbar";
import { DEPLOYMENT_STATUS } from "@/constants/status";
import { shadows } from "@/theme";

export function DeploySection({ service }: { service: Service }): React.ReactElement {
  const serviceId = service.id;
  const queryClient = useQueryClient();
  const [deployOpen, setDeployOpen] = useState(false);
  const [rollingId, setRollingId] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<Deployment | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["deployments", serviceId],
    queryFn: () => listDeployments(serviceId),
  });

  const currentDeploymentId = data?.find((row) => row.status === "success")?.id;

  const handleRollback = async (target: Deployment): Promise<void> => {
    setRollingId(target.id);
    const hide = message.loading("回滚中…", 0);
    try {
      const result = await rollbackService(serviceId, target.id);
      if (isPendingApproval(result)) {
        hide();
        message.info("该回滚为生产高危变更,已提交审批,待审批通过后执行");
        setRollbackTarget(null);
        return;
      }
      const task = await pollTaskUntilDone(result.task_id);
      hide();
      if (task.status === "success") {
        message.success("回滚成功");
      } else if (task.status === "failed") {
        message.error(`回滚失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning("回滚状态未知,请稍后核对");
      }
      setRollbackTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["deployments", serviceId] });
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "回滚请求失败");
    } finally {
      setRollingId(null);
    }
  };

  const columns: ColumnsType<Deployment> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: string | null) => v ?? <Muted />,
    },
    {
      title: "制品",
      key: "artifact",
      render: (_, row) =>
        row.artifact_id ? `${row.artifact_id.slice(0, 8)}…` : (row.artifact ?? <Muted />),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: DeploymentStatus) => {
        const tag = DEPLOYMENT_STATUS[s];
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    { title: "来源", dataIndex: "source", key: "source", width: 130 },
    {
      title: "操作人",
      dataIndex: "operator",
      key: "operator",
      render: (o: string | null) => o ?? <Muted />,
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      key: "started_at",
      render: (t: string | null) => (t ? new Date(t).toLocaleString("zh-CN") : <Muted />),
    },
    {
      title: "操作",
      key: "actions",
      width: 130,
      render: (_, row) => {
        const canRollback =
          row.id !== currentDeploymentId &&
          (row.status === "success" || row.status === "rolled_back");
        return canRollback ? (
          <Button
            danger
            size="small"
            type="link"
            loading={rollingId === row.id}
            disabled={rollingId !== null && rollingId !== row.id}
            onClick={() => setRollbackTarget(row)}
          >
            回滚到此版本
          </Button>
        ) : (
          <Muted />
        );
      },
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载部署历史失败"}
      />
    );
  }

  return (
    <div>
      <TableToolbar
        onRefresh={() => void refetch()}
        refreshing={isFetching}
        actions={
          <Button type="primary" size="small" onClick={() => setDeployOpen(true)}>
            部署
          </Button>
        }
      />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Deployment>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无部署记录,点击右上角部署" }}
          />
        </Card>
      )}

      <DeployServiceModal
        service={service}
        open={deployOpen}
        onClose={() => setDeployOpen(false)}
        onDeployed={() => void queryClient.invalidateQueries({ queryKey: ["deployments", serviceId] })}
      />

      <Modal
        title="确认回滚到此版本"
        open={rollbackTarget !== null}
        okText="确认回滚"
        cancelText="取消"
        okButtonProps={{ danger: true }}
        confirmLoading={rollbackTarget !== null && rollingId === rollbackTarget.id}
        onCancel={() => setRollbackTarget(null)}
        onOk={() => rollbackTarget && void handleRollback(rollbackTarget)}
      >
        {rollbackTarget && (
          <Descriptions
            size="small"
            column={1}
            items={[
              { key: "service", label: "服务", children: `${service.name}(${service.env})` },
              { key: "version", label: "目标版本", children: rollbackTarget.version ?? <Muted /> },
              {
                key: "artifact_id",
                label: "Artifact ID",
                children: rollbackTarget.artifact_id ?? <Muted />,
              },
              {
                key: "started_at",
                label: "部署时间",
                children: rollbackTarget.started_at ? (
                  new Date(rollbackTarget.started_at).toLocaleString("zh-CN")
                ) : (
                  <Muted />
                ),
              },
            ]}
          />
        )}
      </Modal>
    </div>
  );
}

/**
 * 部署与配置页(T2.8,设计 §9/§11/§12)。
 *
 * 选一个服务后分两块:
 * - 部署历史:列出部署记录和制品快照,对明确的历史版本发起定向回滚。
 * - 配置管理:列版本历史、新建版本(内容+格式+说明)、切换生效版(配置回滚)。
 *
 * 部署/回滚异步落 task,提交后轮询 task 到终态再回显(复用 pollTaskUntilDone)。
 */

import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Result,
  Segmented,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { listServices, type Service } from "@/api/services";
import {
  type Deployment,
  type DeploymentStatus,
  type DeploymentStrategy,
  type ScanResult,
  deployService,
  getDeploymentDetail,
  isPendingApproval,
  listDeployments,
  rollbackService,
} from "@/api/deployments";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { Muted } from "@/components/Muted";
import { PageHeader } from "@/components/PageHeader";
import { DEPLOYMENT_STATUS } from "@/constants/status";
import { colors, shadows } from "@/theme";

// 发布策略选项(§11)。canary/blue-green 目前后端仅 k8s+Argo 支持,裸机会明确报错;
// 这里全部列出,由后端按 runtime 决定是否受理,不受理时回显后端的 501 提示。
const STRATEGY_OPTIONS: { label: string; value: DeploymentStrategy }[] = [
  { label: "滚动", value: "rolling" },
  { label: "重建", value: "recreate" },
  { label: "金丝雀", value: "canary" },
  { label: "蓝绿", value: "blue-green" },
];

interface DeployFormValues {
  version: string;
  strategy: DeploymentStrategy;
}

function DeploymentsTab({ service }: { service: Service }): React.ReactElement {
  const serviceId = service.id;
  const queryClient = useQueryClient();
  const [rollingId, setRollingId] = useState<string | null>(null);
  const [rollbackTarget, setRollbackTarget] = useState<Deployment | null>(null);
  const [scanDepId, setScanDepId] = useState<string | null>(null);
  const [deployOpen, setDeployOpen] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [deployForm] = Form.useForm<DeployFormValues>();

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["deployment-detail", serviceId, scanDepId],
    queryFn: () => getDeploymentDetail(serviceId, scanDepId as string),
    enabled: scanDepId !== null,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["deployments", serviceId],
    queryFn: () => listDeployments(serviceId),
  });

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

  // 触发部署:选版本 + 策略。prod 开启审批时后端落 pending 审批(返回 approval_id,
  // 无 task_id),此时提示"已进入审批"而非轮询 task;否则按 task 轮询到终态回显。
  const handleDeploy = async (values: DeployFormValues): Promise<void> => {
    setDeploying(true);
    const hide = message.loading("部署触发中…", 0);
    try {
      const result = await deployService(serviceId, {
        version: values.version,
        strategy: values.strategy,
      });
      if (isPendingApproval(result)) {
        hide();
        message.info("该操作为生产高危变更,已提交审批,待审批通过后执行");
        setDeployOpen(false);
        deployForm.resetFields();
        return;
      }
      const task = await pollTaskUntilDone(result.task_id);
      hide();
      if (task.status === "success") {
        message.success("部署成功");
      } else if (task.status === "failed") {
        message.error(`部署失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning("部署状态未知,请稍后核对");
      }
      setDeployOpen(false);
      deployForm.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["deployments", serviceId] });
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "部署请求失败");
    } finally {
      setDeploying(false);
    }
  };

  const currentDeploymentId = data?.find((row) => row.status === "success")?.id;

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
      width: 210,
      render: (_, row) => {
        const canRollback =
          row.id !== currentDeploymentId &&
          (row.status === "success" || row.status === "rolled_back");
        return (
          <Space size={0}>
            <Button size="small" type="link" onClick={() => setScanDepId(row.id)}>
              查看详情
            </Button>
            {canRollback && (
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
            )}
          </Space>
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
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 12 }}>
        <Button type="primary" onClick={() => setDeployOpen(true)}>
          触发部署
        </Button>
      </div>
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
            locale={{ emptyText: "暂无部署记录" }}
          />
        </Card>
      )}
      <Modal
        title="部署详情与扫描结论"
        open={scanDepId !== null}
        onCancel={() => setScanDepId(null)}
        footer={null}
        width={560}
      >
        {detailLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : detail ? (
          <Space direction="vertical" size="middle">
            <Descriptions
              size="small"
              column={1}
              items={[
                { key: "version", label: "版本", children: detail.deployment.version ?? <Muted /> },
                {
                  key: "artifact_id",
                  label: "Artifact ID",
                  children: detail.deployment.artifact_id ?? <Muted />,
                },
                {
                  key: "artifact",
                  label: "Artifact URI",
                  children: detail.deployment.artifact ?? <Muted />,
                },
              ]}
            />
            {detail.scans.length > 0 ? (
              <Table<ScanResult>
                rowKey="id"
                size="small"
                pagination={false}
                dataSource={detail.scans}
                columns={[
                  { title: "扫描器", dataIndex: "scanner", key: "scanner", width: 110 },
                  {
                    title: "门禁",
                    dataIndex: "passed",
                    key: "passed",
                    width: 80,
                    render: (p: boolean) =>
                      p ? (
                        <Tag color={colors.success}>通过</Tag>
                      ) : (
                        <Tag color={colors.danger}>未过</Tag>
                      ),
                  },
                  {
                    title: "critical",
                    dataIndex: "critical",
                    key: "critical",
                    width: 90,
                    render: (c: number) =>
                      c > 0 ? <span style={{ color: colors.danger }}>{c}</span> : c,
                  },
                  { title: "high", dataIndex: "high", key: "high", width: 70 },
                  { title: "medium", dataIndex: "medium", key: "medium", width: 80 },
                  {
                    title: "报告",
                    dataIndex: "report_url",
                    key: "report_url",
                    render: (u: string | null) =>
                      u ? (
                        <a href={u} target="_blank" rel="noreferrer">
                          查看
                        </a>
                      ) : (
                        <Muted />
                      ),
                  },
                ]}
              />
            ) : (
              <Empty
                description={
                  !detail.deployment.git_sha
                    ? "该部署未关联提交(无 git_sha),无扫描结论"
                    : "无关联扫描结论"
                }
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}
          </Space>
        ) : null}
      </Modal>
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
              { key: "service", label: "服务", children: `${service.name}（${service.env}）` },
              { key: "version", label: "目标版本", children: rollbackTarget.version ?? <Muted /> },
              {
                key: "artifact_id",
                label: "Artifact ID",
                children: rollbackTarget.artifact_id ?? <Muted />,
              },
              {
                key: "artifact",
                label: "Artifact URI",
                children: rollbackTarget.artifact ?? <Muted />,
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
      <Modal
        title="触发部署"
        open={deployOpen}
        onCancel={() => setDeployOpen(false)}
        confirmLoading={deploying}
        okText="部署"
        cancelText="取消"
        onOk={() => deployForm.submit()}
      >
        <Form
          form={deployForm}
          layout="vertical"
          initialValues={{ strategy: "rolling" }}
          onFinish={(v) => void handleDeploy(v)}
        >
          <Form.Item
            name="version"
            label="版本"
            rules={[{ required: true, message: "请输入部署版本" }]}
          >
            <Input placeholder="如 v1.2.3" />
          </Form.Item>
          <Form.Item
            name="strategy"
            label="发布策略"
            extra="k8s 支持 rolling / recreate;canary、蓝绿在裸机(接入负载均衡)可用,k8s 需 Argo Rollouts。"
          >
            <Segmented options={STRATEGY_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

export function DeploymentsPage(): React.ReactElement {
  const [serviceId, setServiceId] = useState<string | undefined>();

  const {
    data: services,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["services"],
    queryFn: () => listServices(),
  });

  const selected = useMemo(
    () => services?.find((s) => s.id === serviceId) ?? services?.[0],
    [services, serviceId],
  );

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载服务列表失败"}
      />
    );
  }
  if (isLoading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  if (!services || services.length === 0) {
    return <Empty description="暂无服务,先在「服务」页创建" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <div>
      <PageHeader
        title="部署与配置"
        inline={
          <Select
            size="small"
            value={selected?.id}
            onChange={setServiceId}
            style={{ width: 220 }}
            options={(services as Service[]).map((s) => ({
              label: `${s.name}（${s.env}）`,
              value: s.id,
            }))}
          />
        }
      />
      {selected && <DeploymentsTab service={selected} />}
    </div>
  );
}

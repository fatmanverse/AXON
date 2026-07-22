/**
 * 构建页(构建能力一期,方案 A「控制面本地构建」)。
 *
 * 选一个服务后分两块:
 * - 构建历史:列出构建记录(版本 / 状态 / git_sha / 来源 / 操作人 / 时间),
 *   一键触发本地构建(填 git_ref / version 覆盖服务 build_config 默认)。
 * - 构建产物:列出该服务产出的制品(名称 / 版本 / 摘要 / 地址 / 大小)。
 *
 * 触发构建异步落 task,提交后轮询 task 到终态再回显。构建远慢于部署,pollTaskUntilDone
 * 显式给足 10 分钟超时;详情(错误 / 产物坐标)走 DetailModal 只读展示。
 */

import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Result,
  Segmented,
  Select,
  Skeleton,
  Table,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { listServices, type Service } from "@/api/services";
import {
  type Artifact,
  type Build,
  type BuildStatus,
  getBuild,
  listArtifacts,
  listBuilds,
  triggerBuild,
} from "@/api/builds";
import { deployService, isPendingApproval } from "@/api/deployments";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { DetailModal } from "@/components/DetailModal";
import { FormModal } from "@/components/FormModal";
import { Muted } from "@/components/Muted";
import { PageHeader } from "@/components/PageHeader";
import { TableToolbar } from "@/components/TableToolbar";
import { BUILD_STATUS } from "@/constants/status";
import { colors, shadows } from "@/theme";
import { Form, Input } from "antd";

// 构建慢(clone+测试+build),轮询超时给足 10 分钟,避免默认 30s 提前判未知。
const BUILD_POLL_TIMEOUT_MS = 600_000;

type BuildTab = "builds" | "artifacts";

interface TriggerFormValues {
  git_ref?: string;
  version?: string;
}

function formatSize(bytes: number | null): string {
  if (bytes === null) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function BuildsTab({ serviceId }: { serviceId: string }): React.ReactElement {
  const queryClient = useQueryClient();
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [form] = Form.useForm<TriggerFormValues>();

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["builds", serviceId],
    queryFn: () => listBuilds(serviceId),
  });

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["build", detailId],
    queryFn: () => getBuild(detailId as string),
    enabled: detailId !== null,
  });

  const handleTrigger = async (values: TriggerFormValues): Promise<void> => {
    setTriggering(true);
    const hide = message.loading("构建触发中,可能需要数分钟…", 0);
    try {
      const accepted = await triggerBuild(serviceId, {
        git_ref: values.git_ref || undefined,
        version: values.version || undefined,
      });
      const task = await pollTaskUntilDone(accepted.task_id, {
        timeoutMs: BUILD_POLL_TIMEOUT_MS,
      });
      hide();
      if (task.status === "success") {
        message.success("构建成功");
      } else if (task.status === "failed") {
        message.error(`构建失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning("构建仍在进行,请稍后刷新核对");
      }
      setTriggerOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["builds", serviceId] });
      void queryClient.invalidateQueries({ queryKey: ["artifacts", serviceId] });
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "构建请求失败");
    } finally {
      setTriggering(false);
    }
  };

  const columns: ColumnsType<Build> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: string | null) => v ?? <Muted />,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: BuildStatus) => {
        const tag = BUILD_STATUS[s];
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    {
      title: "git_sha",
      dataIndex: "git_sha",
      key: "git_sha",
      width: 120,
      render: (v: string | null) =>
        v ? <code>{v.slice(0, 12)}</code> : <Muted />,
    },
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
      render: (t: string | null) =>
        t ? new Date(t).toLocaleString("zh-CN") : <Muted />,
    },
    {
      title: "详情",
      key: "detail",
      width: 80,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => setDetailId(row.id)}>
          详情
        </Button>
      ),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载构建历史失败"}
      />
    );
  }

  return (
    <div>
      <TableToolbar
        onRefresh={() => void refetch()}
        refreshing={isFetching}
        actions={
          <Button type="primary" onClick={() => setTriggerOpen(true)}>
            触发构建
          </Button>
        }
      />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Build>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无构建记录" }}
          />
        </Card>
      )}
      <FormModal<TriggerFormValues>
        title="触发本地构建"
        open={triggerOpen}
        form={form}
        confirmLoading={triggering}
        okText="构建"
        onFinish={(v) => void handleTrigger(v)}
        onClose={() => setTriggerOpen(false)}
      >
        <Form.Item
          name="git_ref"
          label="Git 引用"
          extra="留空则用服务构建配置里的默认分支 / 标签。"
        >
          <Input placeholder="如 main / v1.2.3 / 提交 SHA" />
        </Form.Item>
        <Form.Item name="version" label="版本号" extra="留空则用构建配置默认版本。">
          <Input placeholder="如 1.2.3" />
        </Form.Item>
      </FormModal>
      <DetailModal
        title="构建详情"
        open={detailId !== null}
        onClose={() => setDetailId(null)}
        width={560}
      >
        {detailLoading || !detail ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="状态">
              <Tag color={BUILD_STATUS[detail.status].color}>
                {BUILD_STATUS[detail.status].label}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="仓库">
              {detail.repo_url ?? <Muted />}
            </Descriptions.Item>
            <Descriptions.Item label="Git 引用">
              {detail.git_ref ?? <Muted />}
            </Descriptions.Item>
            <Descriptions.Item label="git_sha">
              {detail.git_sha ? <code>{detail.git_sha}</code> : <Muted />}
            </Descriptions.Item>
            <Descriptions.Item label="版本">
              {detail.version ?? <Muted />}
            </Descriptions.Item>
            <Descriptions.Item label="错误">
              {detail.error ? (
                <span style={{ color: colors.danger }}>{detail.error}</span>
              ) : (
                <Muted />
              )}
            </Descriptions.Item>
          </Descriptions>
        )}
      </DetailModal>
    </div>
  );
}

function ArtifactsTab({ service }: { service: Service }): React.ReactElement {
  const serviceId = service.id;
  const queryClient = useQueryClient();
  const [deployingId, setDeployingId] = useState<string | null>(null);
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["artifacts", serviceId],
    queryFn: () => listArtifacts(serviceId),
  });

  const deployArtifact = async (artifact: Artifact): Promise<void> => {
    setDeployingId(artifact.id);
    const hide = message.loading("制品部署触发中…", 0);
    try {
      const result = await deployService(service.id, {
        artifact_id: artifact.id,
        strategy: "rolling",
      });
      if (isPendingApproval(result)) {
        hide();
        message.info("该操作为生产高危变更,已提交审批,待审批通过后执行");
        return;
      }
      const task = await pollTaskUntilDone(result.task_id);
      hide();
      if (task.status === "success") {
        message.success("制品部署成功");
      } else if (task.status === "failed") {
        message.error(`制品部署失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning("制品部署状态未知,请稍后核对");
      }
      void queryClient.invalidateQueries({ queryKey: ["artifacts", serviceId] });
      void queryClient.invalidateQueries({ queryKey: ["deployments", serviceId] });
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "制品部署请求失败");
    } finally {
      setDeployingId(null);
    }
  };

  const confirmDeploy = (artifact: Artifact): void => {
    Modal.confirm({
      title: "确认部署构建产物",
      content: (
        <Descriptions column={1} size="small">
          <Descriptions.Item label="服务">
            {service.name}({service.env})
          </Descriptions.Item>
          <Descriptions.Item label="运行时">{service.runtime}</Descriptions.Item>
          <Descriptions.Item label="制品">{artifact.name}</Descriptions.Item>
          <Descriptions.Item label="版本">{artifact.version ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="地址">
            <code>{artifact.uri}</code>
          </Descriptions.Item>
        </Descriptions>
      ),
      okText: "部署",
      cancelText: "取消",
      onOk: () => deployArtifact(artifact),
    });
  };

  const columns: ColumnsType<Artifact> = [
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: string | null) => v ?? <Muted />,
    },
    {
      title: "地址",
      dataIndex: "uri",
      key: "uri",
      render: (u: string) => <code>{u}</code>,
    },
    {
      title: "摘要",
      dataIndex: "digest",
      key: "digest",
      width: 140,
      render: (d: string | null) =>
        d ? <code>{d.slice(0, 19)}</code> : <Muted />,
    },
    {
      title: "大小",
      dataIndex: "size_bytes",
      key: "size_bytes",
      width: 90,
      render: (b: number | null) => formatSize(b),
    },
    {
      title: "操作",
      key: "action",
      width: 80,
      render: (_, artifact) => (
        <Button
          size="small"
          type="link"
          loading={deployingId === artifact.id}
          onClick={() => confirmDeploy(artifact)}
        >
          部署
        </Button>
      ),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载构建产物失败"}
      />
    );
  }

  return (
    <div>
      <TableToolbar onRefresh={() => void refetch()} refreshing={isFetching} />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Artifact>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无构建产物" }}
          />
        </Card>
      )}
    </div>
  );
}

export function BuildsPage(): React.ReactElement {
  const [serviceId, setServiceId] = useState<string | undefined>();
  const [tab, setTab] = useState<BuildTab>("builds");

  const { data: services, isLoading, error } = useQuery({
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

  return (
    <div>
      <PageHeader
        title="构建"
        extra={
          <Select
            style={{ minWidth: 220 }}
            loading={isLoading}
            placeholder="选择服务"
            value={selected?.id}
            onChange={setServiceId}
            options={(services ?? []).map((s) => ({
              label: `${s.name}(${s.env})`,
              value: s.id,
            }))}
          />
        }
      />
      {selected ? (
        <>
          <Segmented<BuildTab>
            value={tab}
            onChange={setTab}
            options={[
              { label: "构建历史", value: "builds" },
              { label: "构建产物", value: "artifacts" },
            ]}
            style={{ marginBottom: 16 }}
          />
          {tab === "builds" ? (
            <BuildsTab serviceId={selected.id} />
          ) : (
            <ArtifactsTab service={selected} />
          )}
        </>
      ) : (
        <Empty description="暂无服务,请先在「服务」页创建" />
      )}
    </div>
  );
}

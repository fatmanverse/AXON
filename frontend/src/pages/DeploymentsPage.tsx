/**
 * 部署与配置页(T2.8,设计 §9/§11/§12)。
 *
 * 选一个服务后分两块:
 * - 部署历史:列出部署记录(版本/状态/来源/操作人/时间),一键回滚(Popconfirm
 *   二次确认,走 task 轮询回显)。
 * - 配置管理:列版本历史、新建版本(内容+格式+说明)、切换生效版(配置回滚)。
 *
 * 部署/回滚异步落 task,提交后轮询 task 到终态再回显(复用 pollTaskUntilDone)。
 */

import { useMemo, useState } from "react";
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Result,
  Segmented,
  Select,
  Skeleton,
  Table,
  Tabs,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { listServices, type Service } from "@/api/services";
import {
  type ConfigFormat,
  type ConfigVersion,
  type Deployment,
  type DeploymentStatus,
  type ScanResult,
  activateConfigVersion,
  createConfigVersion,
  getCurrentConfig,
  getDeploymentDetail,
  listConfigVersions,
  listDeployments,
  rollbackService,
} from "@/api/deployments";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { colors } from "@/theme";

const STATUS_TAG: Record<DeploymentStatus, { color: string; label: string }> = {
  running: { color: colors.warning, label: "部署中" },
  success: { color: colors.success, label: "成功" },
  failed: { color: colors.danger, label: "失败" },
  rolled_back: { color: "#8C8C8C", label: "已回滚" },
};

const FORMAT_OPTIONS: { label: string; value: ConfigFormat }[] = [
  { label: "env", value: "env" },
  { label: "yaml", value: "yaml" },
  { label: "properties", value: "properties" },
  { label: "json", value: "json" },
];

function DeploymentsTab({ serviceId }: { serviceId: string }): React.ReactElement {
  const queryClient = useQueryClient();
  const [rolling, setRolling] = useState(false);
  const [scanDepId, setScanDepId] = useState<string | null>(null);

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["deployment-detail", serviceId, scanDepId],
    queryFn: () => getDeploymentDetail(serviceId, scanDepId as string),
    enabled: scanDepId !== null,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["deployments", serviceId],
    queryFn: () => listDeployments(serviceId),
  });

  const handleRollback = async (): Promise<void> => {
    setRolling(true);
    const hide = message.loading("回滚中…", 0);
    try {
      const accepted = await rollbackService(serviceId);
      const task = await pollTaskUntilDone(accepted.task_id);
      hide();
      if (task.status === "success") {
        message.success("回滚成功");
      } else if (task.status === "failed") {
        message.error(`回滚失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning("回滚状态未知,请稍后核对");
      }
      void queryClient.invalidateQueries({ queryKey: ["deployments", serviceId] });
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "回滚请求失败");
    } finally {
      setRolling(false);
    }
  };

  const columns: ColumnsType<Deployment> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: string | null) => v ?? <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: DeploymentStatus) => {
        const tag = STATUS_TAG[s];
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    { title: "来源", dataIndex: "source", key: "source", width: 130 },
    {
      title: "操作人",
      dataIndex: "operator",
      key: "operator",
      render: (o: string | null) => o ?? <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    {
      title: "开始时间",
      dataIndex: "started_at",
      key: "started_at",
      render: (t: string | null) =>
        t ? new Date(t).toLocaleString("zh-CN") : <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    {
      title: "扫描",
      key: "scan",
      width: 90,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => setScanDepId(row.id)}>
          查看扫描
        </Button>
      ),
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
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <Popconfirm
          title="确认回滚到上一版本?"
          description="回滚会重新部署上一次成功的制品,并生成新记录。"
          okText="回滚"
          okButtonProps={{ danger: true, loading: rolling }}
          cancelText="取消"
          onConfirm={() => void handleRollback()}
        >
          <Button danger disabled={rolling}>
            一键回滚
          </Button>
        </Popconfirm>
      </div>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Table<Deployment>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data ?? []}
          pagination={false}
          locale={{ emptyText: "暂无部署记录" }}
          bordered
        />
      )}
      <Modal
        title="扫描结论与门禁"
        open={scanDepId !== null}
        onCancel={() => setScanDepId(null)}
        footer={null}
        width={560}
      >
        {detailLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : detail && detail.scans.length > 0 ? (
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
                  p ? <Tag color={colors.success}>通过</Tag> : <Tag color={colors.danger}>未过</Tag>,
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
                    <span style={{ color: "#B0B3B5" }}>—</span>
                  ),
              },
            ]}
          />
        ) : (
          <Empty
            description={
              detail && !detail.git_sha
                ? "该部署未关联提交(无 git_sha),无扫描结论"
                : "无关联扫描结论"
            }
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        )}
      </Modal>
    </div>
  );
}

function ConfigTab({ serviceId }: { serviceId: string }): React.ReactElement {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<{ content: string; format: ConfigFormat; comment?: string }>();

  const { data: versions, isLoading } = useQuery({
    queryKey: ["configs", serviceId],
    queryFn: () => listConfigVersions(serviceId),
  });
  const { data: current } = useQuery({
    queryKey: ["config-current", serviceId],
    queryFn: () => getCurrentConfig(serviceId),
  });

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["configs", serviceId] });
    void queryClient.invalidateQueries({ queryKey: ["config-current", serviceId] });
  };

  const createMutation = useMutation({
    mutationFn: (body: { content: string; format: ConfigFormat; comment?: string }) =>
      createConfigVersion(serviceId, body),
    onSuccess: (cfg) => {
      message.success(`已保存配置 v${cfg.version}`);
      form.resetFields();
      invalidate();
    },
    onError: (err) => message.error(err instanceof ApiError ? err.message : "保存失败"),
  });

  const activateMutation = useMutation({
    mutationFn: (version: number) => activateConfigVersion(serviceId, version),
    onSuccess: (cfg) => {
      message.success(`已切换到 v${cfg.version}`);
      invalidate();
    },
    onError: (err) => message.error(err instanceof ApiError ? err.message : "切换失败"),
  });

  const columns: ColumnsType<ConfigVersion> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      width: 70,
      render: (v: number, row) => (
        <span>
          v{v}
          {row.is_current && (
            <Tag color={colors.success} style={{ marginLeft: 6 }}>
              生效中
            </Tag>
          )}
        </span>
      ),
    },
    { title: "格式", dataIndex: "format", key: "format", width: 90 },
    {
      title: "说明",
      dataIndex: "comment",
      key: "comment",
      render: (c: string | null) => c ?? <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    { title: "修改人", dataIndex: "created_by", key: "created_by", width: 110 },
    {
      title: "操作",
      key: "actions",
      width: 90,
      render: (_, row) =>
        row.is_current ? (
          <span style={{ color: "#B0B3B5" }}>—</span>
        ) : (
          <Popconfirm
            title={`切换到 v${row.version}?`}
            okText="切换"
            cancelText="取消"
            onConfirm={() => activateMutation.mutate(row.version)}
          >
            <Button size="small" type="link">
              设为生效
            </Button>
          </Popconfirm>
        ),
    },
  ];

  return (
    <div>
      <Card
        size="small"
        title="新建配置版本"
        style={{ marginBottom: 12 }}
        styles={{ header: { fontSize: 13, fontWeight: 600 } }}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{ format: "env" }}
          onFinish={(v) => createMutation.mutate(v)}
        >
          <Form.Item name="format" label="格式">
            <Segmented options={FORMAT_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="content"
            label="配置内容"
            extra="敏感值用 ${secret:名称} 引用,不要写明文密钥。"
            rules={[{ required: true, message: "请输入配置内容" }]}
          >
            <Input.TextArea rows={6} placeholder={"A=1\nB=2"} />
          </Form.Item>
          <Form.Item name="comment" label="变更说明(可选)">
            <Input placeholder="如 调高连接池上限" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
              保存新版本
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {current && (
        <div style={{ marginBottom: 8, fontSize: 13, color: colors.textBody }}>
          当前生效:v{current.version}
        </div>
      )}
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : (
        <Table<ConfigVersion>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={versions ?? []}
          pagination={false}
          locale={{ emptyText: "暂无配置版本" }}
          bordered
        />
      )}
    </div>
  );
}

export function DeploymentsPage(): React.ReactElement {
  const [serviceId, setServiceId] = useState<string | undefined>();

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
  if (isLoading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  if (!services || services.length === 0) {
    return (
      <Empty
        description="暂无服务,先在「服务」页创建"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.textTitle }}>
          部署与配置
        </span>
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
      </div>
      {selected && (
        <Tabs
          items={[
            {
              key: "deployments",
              label: "部署历史",
              children: <DeploymentsTab serviceId={selected.id} />,
            },
            {
              key: "configs",
              label: "配置管理",
              children: <ConfigTab serviceId={selected.id} />,
            },
          ]}
        />
      )}
    </div>
  );
}

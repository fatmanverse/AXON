/**
 * 服务列表与生命周期操作页(T1.17,设计 §15.2 / §4)。
 *
 * 表格为主体:列出服务(名称/环境/运行时/放置数/期望版本),支持按 env/runtime
 * 过滤。每行提供统一的启停/重启/删除按钮——底层 systemd/docker/k8s 多态无感,
 * 前端只认 task 语义:动作异步落 task,提交后轮询 task 进度并回显终态。删除走
 * 二次确认。新建服务用 Drawer 表单。
 */

import { useState } from "react";
import {
  Button,
  Drawer,
  Form,
  Input,
  Popconfirm,
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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  type CreateServiceRequest,
  type LifecycleAction,
  type ListServicesParams,
  type Runtime,
  type Service,
  type ServiceEnvironment,
  createService,
  listServices,
  runLifecycle,
} from "@/api/services";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { colors } from "@/theme";

const ENV_TAG: Record<ServiceEnvironment, string> = {
  dev: "default",
  staging: colors.warning,
  prod: colors.danger,
};

const RUNTIME_OPTIONS: { label: string; value: Runtime }[] = [
  { label: "systemd", value: "systemd" },
  { label: "docker", value: "docker" },
  { label: "k8s", value: "k8s" },
  { label: "process", value: "process" },
  { label: "cloud-fn", value: "cloud-fn" },
];

const ENV_OPTIONS: { label: string; value: ServiceEnvironment }[] = [
  { label: "dev", value: "dev" },
  { label: "staging", value: "staging" },
  { label: "prod", value: "prod" },
];

// 各 runtime 的 runtime_ref 目标键:与后端 runtime_registry 对齐。
const RUNTIME_REF_KEY: Record<Runtime, string> = {
  systemd: "unit_name",
  docker: "container_name",
  k8s: "workload",
  process: "command",
  "cloud-fn": "function_name",
};

const ACTION_LABEL: Record<LifecycleAction, string> = {
  start: "启动",
  stop: "停止",
  restart: "重启",
  delete: "删除",
};

interface ServiceFormValues {
  name: string;
  env: ServiceEnvironment;
  runtime: Runtime;
  target: string;
  desired_version?: string;
}

function toCreateRequest(values: ServiceFormValues): CreateServiceRequest {
  return {
    name: values.name,
    env: values.env,
    runtime: values.runtime,
    runtime_ref: { [RUNTIME_REF_KEY[values.runtime]]: values.target },
    desired_version: values.desired_version || null,
  };
}

export function ServicesPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [envFilter, setEnvFilter] = useState<ServiceEnvironment | undefined>();
  const [runtimeFilter, setRuntimeFilter] = useState<Runtime | undefined>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [form] = Form.useForm<ServiceFormValues>();

  const filters: ListServicesParams = { env: envFilter, runtime: runtimeFilter };
  const { data, isLoading, error } = useQuery({
    queryKey: ["services", envFilter, runtimeFilter],
    queryFn: () => listServices(filters),
  });

  const createMutation = useMutation({
    mutationFn: (body: CreateServiceRequest) => createService(body),
    onSuccess: (service) => {
      message.success(`已创建服务 ${service.name}`);
      setDrawerOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["services"] });
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "创建失败");
    },
  });

  const handleAction = async (
    service: Service,
    action: LifecycleAction,
  ): Promise<void> => {
    const label = ACTION_LABEL[action];
    setBusyId(service.id);
    const hide = message.loading(`${service.name} ${label}中…`, 0);
    try {
      const accepted = await runLifecycle(service.id, action);
      const task = await pollTaskUntilDone(accepted.task_id);
      hide();
      if (task.status === "success") {
        message.success(`${service.name} ${label}成功`);
      } else if (task.status === "failed") {
        message.error(`${service.name} ${label}失败:${task.error ?? "未知错误"}`);
      } else {
        message.warning(`${service.name} ${label}状态未知,请稍后核对`);
      }
      if (action === "delete") {
        void queryClient.invalidateQueries({ queryKey: ["services"] });
      }
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : `${label}请求失败`);
    } finally {
      setBusyId(null);
    }
  };

  const columns: ColumnsType<Service> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string) => <span style={{ color: colors.textTitle }}>{name}</span>,
    },
    {
      title: "环境",
      dataIndex: "env",
      key: "env",
      width: 90,
      render: (env: ServiceEnvironment) => <Tag color={ENV_TAG[env]}>{env}</Tag>,
    },
    {
      title: "运行时",
      dataIndex: "runtime",
      key: "runtime",
      width: 100,
      render: (rt: Runtime) => <Tag>{rt}</Tag>,
    },
    {
      title: "放置数",
      dataIndex: "placement_count",
      key: "placement_count",
      width: 80,
    },
    {
      title: "期望版本",
      dataIndex: "desired_version",
      key: "desired_version",
      render: (v: string | null) =>
        v ? v : <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    {
      title: "操作",
      key: "actions",
      width: 260,
      render: (_, row) => {
        const busy = busyId === row.id;
        return (
          <Space size="small">
            <Button
              size="small"
              type="link"
              disabled={busy}
              onClick={() => void handleAction(row, "start")}
            >
              启动
            </Button>
            <Button
              size="small"
              type="link"
              disabled={busy}
              onClick={() => void handleAction(row, "stop")}
            >
              停止
            </Button>
            <Button
              size="small"
              type="link"
              disabled={busy}
              onClick={() => void handleAction(row, "restart")}
            >
              重启
            </Button>
            <Popconfirm
              title="确认删除该服务?"
              description="删除为高危操作,prod 环境需相应权限。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => void handleAction(row, "delete")}
            >
              <Button size="small" type="link" danger disabled={busy}>
                删除
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

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
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <Space size="middle">
          <span style={{ fontSize: 14, fontWeight: 600, color: colors.textTitle }}>
            服务
          </span>
          <Select<ServiceEnvironment>
            size="small"
            allowClear
            placeholder="全部环境"
            value={envFilter}
            onChange={setEnvFilter}
            options={ENV_OPTIONS}
            style={{ width: 120 }}
          />
          <Select<Runtime>
            size="small"
            allowClear
            placeholder="全部运行时"
            value={runtimeFilter}
            onChange={setRuntimeFilter}
            options={RUNTIME_OPTIONS}
            style={{ width: 130 }}
          />
        </Space>
        <Button type="primary" onClick={() => setDrawerOpen(true)}>
          新建服务
        </Button>
      </div>

      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : (
        <Table<Service>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data ?? []}
          pagination={false}
          locale={{ emptyText: "暂无服务,点击右上角新建" }}
          bordered
        />
      )}

      <Drawer
        title="新建服务"
        width={420}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
      >
        <Form<ServiceFormValues>
          form={form}
          layout="vertical"
          onFinish={(values) => createMutation.mutate(toCreateRequest(values))}
          requiredMark={false}
          initialValues={{ env: "dev", runtime: "systemd" }}
        >
          <Form.Item
            name="name"
            label="服务名"
            rules={[{ required: true, message: "请输入服务名" }]}
          >
            <Input placeholder="如 billing" />
          </Form.Item>
          <Form.Item name="env" label="环境">
            <Segmented options={ENV_OPTIONS} />
          </Form.Item>
          <Form.Item name="runtime" label="运行时">
            <Select options={RUNTIME_OPTIONS} />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.runtime !== cur.runtime}
          >
            {({ getFieldValue }) => {
              const rt = (getFieldValue("runtime") as Runtime) ?? "systemd";
              return (
                <Form.Item
                  name="target"
                  label={`目标标识(${RUNTIME_REF_KEY[rt]})`}
                  rules={[{ required: true, message: "请输入运行时目标标识" }]}
                  extra="如 systemd 填 unit 名、docker 填容器名、k8s 填 workload。"
                >
                  <Input placeholder="如 billing.service" />
                </Form.Item>
              );
            }}
          </Form.Item>
          <Form.Item name="desired_version" label="期望版本(可选)">
            <Input placeholder="如 v1.2.0" />
          </Form.Item>
          <Form.Item style={{ marginTop: 8, marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
                创建
              </Button>
              <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}

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
  Card,
  Descriptions,
  Form,
  Input,
  Popconfirm,
  Result,
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
import { type Environment, listEnvironments } from "@/api/environments";
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
import { DetailModal } from "@/components/DetailModal";
import { FormModal } from "@/components/FormModal";
import { TableToolbar, type ColumnToggle } from "@/components/TableToolbar";
import { colors, shadows } from "@/theme";
import { Muted } from "@/components/Muted";

// 环境标签配色:环境为任意自定义名,不再按名硬编码。改按环境语义着色——需审批的
// 高危环境用 danger 强提示,普通环境用 default;环境已被删(悬空引用)时也走 default。
function envTagColor(env: Environment | undefined): string {
  if (env?.requires_approval) {
    return colors.danger;
  }
  return "default";
}

const RUNTIME_OPTIONS: { label: string; value: Runtime }[] = [
  { label: "systemd", value: "systemd" },
  { label: "docker", value: "docker" },
  { label: "k8s", value: "k8s" },
  { label: "process", value: "process" },
  { label: "cloud-fn", value: "cloud-fn" },
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

// 可显隐的列(排除「名称」身份列与「操作」固定列)。
const TOGGLEABLE_COLUMNS: ColumnToggle[] = [
  { key: "env", label: "环境" },
  { key: "runtime", label: "运行时" },
  { key: "placement_count", label: "放置数" },
  { key: "desired_version", label: "期望版本" },
];
const DEFAULT_VISIBLE = TOGGLEABLE_COLUMNS.map((c) => c.key);

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

/** 服务详情弹窗:只读展示列表已有字段(环境/运行时/放置数/期望版本/运行时目标)。 */
function ServiceDetailModal({
  service,
  envColor,
  onClose,
}: {
  service: Service | null;
  envColor: string;
  onClose: () => void;
}): React.ReactElement {
  return (
    <DetailModal
      title={service ? `服务详情 · ${service.name}` : "服务详情"}
      open={service != null}
      onClose={onClose}
    >
      {service && (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="服务名">{service.name}</Descriptions.Item>
          <Descriptions.Item label="环境">
            <Tag color={envColor}>{service.env}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="运行时">
            <Tag>{service.runtime}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="放置数">{service.placement_count}</Descriptions.Item>
          <Descriptions.Item label="期望版本">
            {service.desired_version ?? <Muted />}
          </Descriptions.Item>
          <Descriptions.Item label="重启方式">{service.reload_mode}</Descriptions.Item>
          <Descriptions.Item label="运行时目标">
            <pre
              style={{
                margin: 0,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                color: colors.textBody,
              }}
            >
              {JSON.stringify(service.runtime_ref, null, 2)}
            </pre>
          </Descriptions.Item>
        </Descriptions>
      )}
    </DetailModal>
  );
}

export function ServicesPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [envFilter, setEnvFilter] = useState<ServiceEnvironment | undefined>();
  const [runtimeFilter, setRuntimeFilter] = useState<Runtime | undefined>();
  const [modalOpen, setModalOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [visibleColumns, setVisibleColumns] = useState<string[]>(DEFAULT_VISIBLE);
  const [detailService, setDetailService] = useState<Service | null>(null);
  const [form] = Form.useForm<ServiceFormValues>();

  const filters: ListServicesParams = { env: envFilter, runtime: runtimeFilter };
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["services", envFilter, runtimeFilter],
    queryFn: () => listServices(filters),
  });

  // 环境列表:与服务器纳管页同源,供筛选/新建下拉与标签着色。按 name 建索引,
  // 表格/详情据 service.env 反查环境语义(requires_approval)着色。
  const { data: environments } = useQuery({
    queryKey: ["environments"],
    queryFn: listEnvironments,
  });
  const envByName = new Map((environments ?? []).map((e) => [e.name, e]));
  const envOptions = (environments ?? []).map((e) => ({
    label: e.display_name ? `${e.display_name} (${e.name})` : e.name,
    value: e.name,
  }));

  const createMutation = useMutation({
    mutationFn: (body: CreateServiceRequest) => createService(body),
    onSuccess: (service) => {
      message.success(`已创建服务 ${service.name}`);
      setModalOpen(false);
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
      render: (name: string, row) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setDetailService(row)}>
          {name}
        </Button>
      ),
    },
    {
      title: "环境",
      dataIndex: "env",
      key: "env",
      width: 90,
      render: (env: ServiceEnvironment) => (
        <Tag color={envTagColor(envByName.get(env))}>{env}</Tag>
      ),
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
        v ? v : <Muted />,
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

  // 「名称」「操作」为固定列,其余按可见集合裁剪;搜索按服务名前端即时过滤。
  const visibleTableColumns = columns.filter(
    (col) =>
      col.key === "name" ||
      col.key === "actions" ||
      visibleColumns.includes(col.key as string),
  );
  const keyword = search.trim().toLowerCase();
  const filteredData = (data ?? []).filter(
    (s) => !keyword || s.name.toLowerCase().includes(keyword),
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
      <TableToolbar
        title="服务"
        filters={
          <>
            <Select<ServiceEnvironment>
              size="small"
              allowClear
              placeholder="全部环境"
              value={envFilter}
              onChange={setEnvFilter}
              options={envOptions}
              style={{ width: 140 }}
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
          </>
        }
        searchValue={search}
        onSearchChange={setSearch}
        searchPlaceholder="搜索服务名"
        onRefresh={() => void refetch()}
        refreshing={isFetching}
        columnSettings={{
          options: TOGGLEABLE_COLUMNS,
          value: visibleColumns,
          onChange: setVisibleColumns,
        }}
        actions={
          <Button type="primary" size="small" onClick={() => setModalOpen(true)}>
            新建服务
          </Button>
        }
      />

      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Service>
            rowKey="id"
            size="small"
            columns={visibleTableColumns}
            dataSource={filteredData}
            pagination={{ pageSize: 15, hideOnSinglePage: true, showSizeChanger: false }}
            locale={{ emptyText: "暂无服务,点击右上角新建" }}
          />
        </Card>
      )}

      <FormModal<ServiceFormValues>
        title="新建服务"
        open={modalOpen}
        form={form}
        onFinish={(values) => createMutation.mutate(toCreateRequest(values))}
        onClose={() => setModalOpen(false)}
        confirmLoading={createMutation.isPending}
        okText="创建"
        initialValues={{ runtime: "systemd" }}
      >
        <Form.Item
          name="name"
          label="服务名"
          rules={[{ required: true, message: "请输入服务名" }]}
        >
          <Input placeholder="如 billing" />
        </Form.Item>
        <Form.Item
          name="env"
          label="环境"
          rules={[{ required: true, message: "请选择归属环境" }]}
          extra="从环境管理中已创建的环境里选择;若无可选,请先到环境管理创建。"
        >
          <Select
            placeholder="选择环境"
            options={envOptions}
            notFoundContent="暂无环境,请先在环境管理创建"
          />
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
      </FormModal>

      <ServiceDetailModal
        service={detailService}
        envColor={envTagColor(detailService ? envByName.get(detailService.env) : undefined)}
        onClose={() => setDetailService(null)}
      />
    </div>
  );
}

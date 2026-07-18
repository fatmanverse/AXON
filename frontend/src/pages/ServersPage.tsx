/**
 * 服务器列表页(需求2/3/4,设计 §3.2)。
 *
 * 表格为主体:列出纳管服务器(接入模式 / 归属环境 / Agent 在线状态)。纳管表单
 * 支持 SSH(私钥或密码二选一)与 Agent 两种模式,归属环境从环境管理已建环境中选。
 * SSH 服务器可一键经 SSH 下发安装 Agent(需求4)。凭证仅在提交时传一次,响应不
 * 回传、前端不缓存(§13)。
 */

import { useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
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
import { listEnvironments } from "@/api/environments";
import {
  type AccessMode,
  type AgentStatus,
  type RegisterServerRequest,
  type Server,
  type SshAuthType,
  deleteServer,
  installAgent,
  listServers,
  registerServer,
  testConnection,
} from "@/api/servers";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { AGENT_STATUS } from "@/constants/status";
import { FormModal } from "@/components/FormModal";
import { Muted } from "@/components/Muted";
import { PageHeader } from "@/components/PageHeader";
import { colors, shadows } from "@/theme";

interface ServerFormValues {
  name: string;
  host: string;
  environment: string;
  auth_type?: SshAuthType;
  username?: string;
  ssh_private_key?: string;
  ssh_password?: string;
  ssh_port?: number;
  agent_id?: string;
}

function toRequest(
  mode: AccessMode,
  authType: SshAuthType,
  values: ServerFormValues,
): RegisterServerRequest {
  if (mode === "ssh") {
    return {
      name: values.name,
      host: values.host,
      access_mode: "ssh",
      environment: values.environment,
      auth_type: authType,
      username: values.username,
      ssh_private_key: authType === "key" ? values.ssh_private_key : undefined,
      ssh_password: authType === "password" ? values.ssh_password : undefined,
      ssh_port: values.ssh_port ?? 22,
    };
  }
  return {
    name: values.name,
    host: values.host,
    access_mode: "agent",
    environment: values.environment,
    agent_id: values.agent_id ?? "",
  };
}

export function ServersPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [modalOpen, setModalOpen] = useState(false);
  const [mode, setMode] = useState<AccessMode>("ssh");
  const [authType, setAuthType] = useState<SshAuthType>("key");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [form] = Form.useForm<ServerFormValues>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["servers"],
    queryFn: listServers,
  });

  const { data: environments } = useQuery({
    queryKey: ["environments"],
    queryFn: listEnvironments,
  });

  const registerMutation = useMutation({
    mutationFn: (body: RegisterServerRequest) => registerServer(body),
    onSuccess: (server) => {
      message.success(`已纳管服务器 ${server.name}`);
      setModalOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["servers"] });
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "纳管失败");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (serverId: string) => deleteServer(serverId),
    onSuccess: () => {
      message.success("已删除");
      void queryClient.invalidateQueries({ queryKey: ["servers"] });
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "删除失败");
    },
  });

  const handleTest = async (server: Server): Promise<void> => {
    setTestingId(server.id);
    try {
      const result = await testConnection(server.id);
      if (result.reachable) {
        message.success(`${server.name} 连通正常`);
      } else {
        message.warning(`${server.name} 无法连通`);
      }
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "连通性测试失败");
    } finally {
      setTestingId(null);
    }
  };

  const handleInstallAgent = async (server: Server): Promise<void> => {
    setInstallingId(server.id);
    try {
      const accepted = await installAgent(server.id);
      const task = await pollTaskUntilDone(accepted.task_id);
      if (task.status === "success") {
        message.success(`${server.name} Agent 安装完成`);
      } else {
        message.error(`${server.name} Agent 安装失败: ${task.error ?? task.status}`);
      }
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "Agent 下发失败");
    } finally {
      setInstallingId(null);
    }
  };

  const handleSubmit = (values: ServerFormValues): void => {
    registerMutation.mutate(toRequest(mode, authType, values));
  };

  const columns: ColumnsType<Server> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      render: (name: string) => <span style={{ color: colors.textTitle }}>{name}</span>,
    },
    { title: "主机", dataIndex: "host", key: "host" },
    {
      title: "环境",
      dataIndex: "environment",
      key: "environment",
      width: 100,
      render: (env: string | null) =>
        env ? <Tag>{env}</Tag> : <Muted />,
    },
    {
      title: "接入模式",
      dataIndex: "access_mode",
      key: "access_mode",
      width: 100,
      render: (m: AccessMode) => (
        <Tag color={m === "ssh" ? colors.info : colors.primary}>{m.toUpperCase()}</Tag>
      ),
    },
    {
      title: "Agent 状态",
      dataIndex: "agent_status",
      key: "agent_status",
      width: 100,
      render: (status: AgentStatus, row) => {
        if (row.access_mode === "ssh") {
          return <Muted />;
        }
        const tag = AGENT_STATUS[status];
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 260,
      render: (_, row) => (
        <Space size="small">
          <Button
            size="small"
            type="link"
            loading={testingId === row.id}
            disabled={row.access_mode !== "ssh"}
            onClick={() => void handleTest(row)}
          >
            连通性测试
          </Button>
          <Button
            size="small"
            type="link"
            loading={installingId === row.id}
            disabled={row.access_mode !== "ssh"}
            onClick={() => void handleInstallAgent(row)}
          >
            安装 Agent
          </Button>
          <Popconfirm
            title="确认删除该服务器?"
            description="删除后不可恢复,相关放置将一并移除。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={() => deleteMutation.mutate(row.id)}
          >
            <Button size="small" type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载服务器列表失败"}
      />
    );
  }

  const envOptions = (environments ?? []).map((e) => ({
    label: e.display_name ? `${e.display_name} (${e.name})` : e.name,
    value: e.name,
  }));

  return (
    <div>
      <PageHeader
        title="服务器"
        extra={
          <Button type="primary" onClick={() => setModalOpen(true)}>
            纳管服务器
          </Button>
        }
      />

      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Server>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无纳管服务器,点击右上角纳管第一台" }}
          />
        </Card>
      )}

      <FormModal<ServerFormValues>
        title="纳管服务器"
        open={modalOpen}
        form={form}
        onFinish={handleSubmit}
        onClose={() => setModalOpen(false)}
        confirmLoading={registerMutation.isPending}
        okText="纳管"
      >
        <Form.Item label="接入模式">
          <Segmented
            value={mode}
            onChange={(v) => setMode(v as AccessMode)}
            options={[
              { label: "SSH", value: "ssh" },
              { label: "Agent", value: "agent" },
            ]}
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, message: "请输入服务器名称" }]}
        >
          <Input placeholder="如 web-01" />
        </Form.Item>
        <Form.Item
          name="host"
          label="主机地址"
          rules={[{ required: true, message: "请输入主机 IP 或域名" }]}
        >
          <Input placeholder="如 10.0.0.10" />
        </Form.Item>
        <Form.Item
          name="environment"
          label="归属环境"
          rules={[{ required: true, message: "请选择归属环境" }]}
          extra="从环境管理中已创建的环境里选择;若无可选,请先到环境管理创建。"
        >
          <Select
            placeholder="选择环境"
            options={envOptions}
            notFoundContent="暂无环境,请先在环境管理创建"
          />
        </Form.Item>

        {mode === "ssh" ? (
          <>
            <Form.Item label="认证方式">
              <Segmented
                value={authType}
                onChange={(v) => setAuthType(v as SshAuthType)}
                options={[
                  { label: "私钥", value: "key" },
                  { label: "密码", value: "password" },
                ]}
              />
            </Form.Item>
            <Form.Item name="username" label="SSH 用户名">
              <Input placeholder="默认 root" />
            </Form.Item>
            <Form.Item name="ssh_port" label="SSH 端口" initialValue={22}>
              <InputNumber min={1} max={65535} style={{ width: "100%" }} />
            </Form.Item>
            {authType === "key" ? (
              <Form.Item
                name="ssh_private_key"
                label="SSH 私钥"
                rules={[{ required: true, message: "请粘贴 SSH 私钥" }]}
                extra="私钥仅用于建连,存入凭证保险箱,不落业务库、不回显。"
              >
                <Input.TextArea rows={5} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
              </Form.Item>
            ) : (
              <Form.Item
                name="ssh_password"
                label="SSH 密码"
                rules={[{ required: true, message: "请输入 SSH 密码" }]}
                extra="密码存入凭证保险箱,不落业务库、不回显。"
              >
                <Input.Password placeholder="SSH 登录密码" />
              </Form.Item>
            )}
          </>
        ) : (
          <Form.Item
            name="agent_id"
            label="Agent ID"
            rules={[{ required: true, message: "请输入 Agent ID" }]}
            extra="Agent 模式下由 Agent 主动上报心跳,无需 SSH 凭证。"
          >
            <Input placeholder="Agent 注册时分配的 ID" />
          </Form.Item>
        )}
      </FormModal>
    </div>
  );
}

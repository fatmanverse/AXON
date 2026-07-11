/**
 * 服务器列表页(T1.16,设计 §3.2)。
 *
 * 表格为主体:列出纳管服务器(接入模式 / Agent 在线状态 / 连通性),提供
 * 纳管表单(SSH 填私钥 / Agent 填 agent_id)、逐行连通性测试与删除。
 * 私钥仅在提交时传一次,响应不回传、前端不缓存(§13)。
 */

import { useState } from "react";
import {
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Result,
  Segmented,
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
  type AccessMode,
  type AgentStatus,
  type RegisterServerRequest,
  type Server,
  deleteServer,
  listServers,
  registerServer,
  testConnection,
} from "@/api/servers";
import { colors } from "@/theme";

const AGENT_STATUS_TAG: Record<AgentStatus, { color: string; label: string }> = {
  online: { color: colors.success, label: "在线" },
  offline: { color: colors.danger, label: "离线" },
  unknown: { color: "default", label: "未知" },
};

interface ServerFormValues {
  name: string;
  host: string;
  username?: string;
  ssh_private_key?: string;
  ssh_port?: number;
  agent_id?: string;
}

function toRequest(mode: AccessMode, values: ServerFormValues): RegisterServerRequest {
  if (mode === "ssh") {
    return {
      name: values.name,
      host: values.host,
      access_mode: "ssh",
      username: values.username,
      ssh_private_key: values.ssh_private_key ?? "",
      ssh_port: values.ssh_port ?? 22,
    };
  }
  return {
    name: values.name,
    host: values.host,
    access_mode: "agent",
    agent_id: values.agent_id ?? "",
  };
}

export function ServersPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [mode, setMode] = useState<AccessMode>("ssh");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [form] = Form.useForm<ServerFormValues>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["servers"],
    queryFn: listServers,
  });

  const registerMutation = useMutation({
    mutationFn: (body: RegisterServerRequest) => registerServer(body),
    onSuccess: (server) => {
      message.success(`已纳管服务器 ${server.name}`);
      setDrawerOpen(false);
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

  const handleSubmit = (values: ServerFormValues): void => {
    registerMutation.mutate(toRequest(mode, values));
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
      title: "接入模式",
      dataIndex: "access_mode",
      key: "access_mode",
      width: 110,
      render: (m: AccessMode) => (
        <Tag color={m === "ssh" ? colors.info : colors.primary}>{m.toUpperCase()}</Tag>
      ),
    },
    {
      title: "Agent 状态",
      dataIndex: "agent_status",
      key: "agent_status",
      width: 110,
      render: (status: AgentStatus, row) => {
        if (row.access_mode === "ssh") {
          return <span style={{ color: "#B0B3B5" }}>—</span>;
        }
        const tag = AGENT_STATUS_TAG[status];
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 180,
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
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.textTitle }}>
          服务器
        </span>
        <Button type="primary" onClick={() => setDrawerOpen(true)}>
          纳管服务器
        </Button>
      </div>

      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : (
        <Table<Server>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data ?? []}
          pagination={false}
          locale={{ emptyText: "暂无纳管服务器,点击右上角纳管第一台" }}
          bordered
        />
      )}

      <Drawer
        title="纳管服务器"
        width={420}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
      >
        <Form<ServerFormValues>
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          requiredMark={false}
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

          {mode === "ssh" ? (
            <>
              <Form.Item name="username" label="SSH 用户名">
                <Input placeholder="默认 root" />
              </Form.Item>
              <Form.Item name="ssh_port" label="SSH 端口" initialValue={22}>
                <InputNumber min={1} max={65535} style={{ width: "100%" }} />
              </Form.Item>
              <Form.Item
                name="ssh_private_key"
                label="SSH 私钥"
                rules={[{ required: true, message: "请粘贴 SSH 私钥" }]}
                extra="私钥仅用于建连,存入凭证保险箱,不落业务库、不回显。"
              >
                <Input.TextArea rows={5} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
              </Form.Item>
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

          <Form.Item style={{ marginTop: 8, marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit" loading={registerMutation.isPending}>
                纳管
              </Button>
              <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}

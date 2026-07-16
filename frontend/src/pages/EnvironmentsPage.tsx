/**
 * 环境管理页(需求1,设计 §10.1)。
 *
 * 自定义环境的列表与创建:每个环境自带 requires_approval 开关,决定该环境的
 * 高危操作(部署/删除/回滚)是否走审批闸门(§10.2)。服务器纳管与服务创建时
 * 从这里建好的环境中选择归属。
 */

import { useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Result,
  Skeleton,
  Space,
  Switch,
  Table,
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  type CreateEnvironmentRequest,
  type Environment,
  createEnvironment,
  deleteEnvironment,
  listEnvironments,
} from "@/api/environments";
import { PageHeader } from "@/components/PageHeader";
import { colors, shadows } from "@/theme";

interface EnvFormValues {
  name: string;
  display_name?: string;
  requires_approval?: boolean;
  description?: string;
}

export function EnvironmentsPage(): React.ReactElement {
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [form] = Form.useForm<EnvFormValues>();

  const { data, isLoading, error } = useQuery({
    queryKey: ["environments"],
    queryFn: listEnvironments,
  });

  const createMutation = useMutation({
    mutationFn: (body: CreateEnvironmentRequest) => createEnvironment(body),
    onSuccess: (env) => {
      message.success(`已创建环境 ${env.name}`);
      setDrawerOpen(false);
      form.resetFields();
      void queryClient.invalidateQueries({ queryKey: ["environments"] });
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "创建失败");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteEnvironment(id),
    onSuccess: () => {
      message.success("已删除");
      void queryClient.invalidateQueries({ queryKey: ["environments"] });
    },
    onError: (err) => {
      message.error(err instanceof ApiError ? err.message : "删除失败");
    },
  });

  const handleSubmit = (values: EnvFormValues): void => {
    createMutation.mutate({
      name: values.name,
      display_name: values.display_name,
      requires_approval: values.requires_approval ?? false,
      description: values.description,
    });
  };

  const columns: ColumnsType<Environment> = [
    {
      title: "标识",
      dataIndex: "name",
      key: "name",
      render: (name: string) => <span style={{ color: colors.textTitle }}>{name}</span>,
    },
    { title: "显示名", dataIndex: "display_name", key: "display_name" },
    {
      title: "审批闸门",
      dataIndex: "requires_approval",
      key: "requires_approval",
      width: 120,
      render: (req: boolean) =>
        req ? (
          <Tag color={colors.warning}>需审批</Tag>
        ) : (
          <Tag color="default">直接执行</Tag>
        ),
    },
    { title: "描述", dataIndex: "description", key: "description" },
    {
      title: "操作",
      key: "actions",
      width: 100,
      render: (_, row) => (
        <Popconfirm
          title="确认删除该环境?"
          description="删除前请确保没有服务或服务器仍归属此环境。"
          okText="删除"
          okButtonProps={{ danger: true }}
          cancelText="取消"
          onConfirm={() => deleteMutation.mutate(row.id)}
        >
          <Button size="small" type="link" danger>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载环境列表失败"}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="环境"
        extra={
          <Button type="primary" onClick={() => setDrawerOpen(true)}>
            创建环境
          </Button>
        }
      />

      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Environment>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无环境,点击右上角创建第一个" }}
          />
        </Card>
      )}

      <Drawer
        title="创建环境"
        width={420}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
      >
        <Form<EnvFormValues>
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          requiredMark={false}
        >
          <Form.Item
            name="name"
            label="环境标识"
            rules={[
              { required: true, message: "请输入环境标识" },
              {
                pattern: /^[a-z0-9][a-z0-9-]*$/,
                message: "小写字母/数字/连字符,以字母或数字开头",
              },
            ]}
            extra="稳定唯一标识,创建后作为服务与服务器的归属键,如 dev / staging / prod / gray。"
          >
            <Input placeholder="如 gray" />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input placeholder="如 灰度环境" />
          </Form.Item>
          <Form.Item
            name="requires_approval"
            label="高危操作需审批"
            valuePropName="checked"
            initialValue={false}
            extra="开启后,该环境的部署/删除/回滚先落审批,由授权人批准后执行(§10.2)。"
          >
            <Switch />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} placeholder="用途说明(可选)" />
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

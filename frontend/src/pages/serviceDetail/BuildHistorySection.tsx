/**
 * 服务详情页「构建」分区(块三)。
 *
 * 复用「构建」顶级页的构建历史与触发逻辑,但聚焦单服务:列出该服务的构建记录,
 * 一键触发本地构建(可覆盖 git_ref / version),提交后轮询 task 到终态再刷新。
 * 制品产出在「部署」分区消费(统一部署入口),此处只管「代码→制品」的前半段。
 */

import { useState } from "react";
import { Button, Card, Form, Input, Result, Skeleton, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { type Build, type BuildStatus, listBuilds, triggerBuild } from "@/api/builds";
import type { Service } from "@/api/services";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { FormModal } from "@/components/FormModal";
import { Muted } from "@/components/Muted";
import { TableToolbar } from "@/components/TableToolbar";
import { BUILD_STATUS } from "@/constants/status";
import { shadows } from "@/theme";

// 构建慢(clone+测试+build),轮询超时给足 10 分钟,避免默认 30s 提前判未知。
const BUILD_POLL_TIMEOUT_MS = 600_000;

interface TriggerFormValues {
  git_ref?: string;
  version?: string;
}

export function BuildHistorySection({ service }: { service: Service }): React.ReactElement {
  const serviceId = service.id;
  const queryClient = useQueryClient();
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [form] = Form.useForm<TriggerFormValues>();

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["builds", serviceId],
    queryFn: () => listBuilds(serviceId),
  });

  const handleTrigger = async (values: TriggerFormValues): Promise<void> => {
    if (!service.build_config) {
      message.warning("该服务未配置构建,请先在「服务」列表页编辑构建配置");
      return;
    }
    setTriggering(true);
    const hide = message.loading("构建触发中,可能需要数分钟…", 0);
    try {
      const accepted = await triggerBuild(serviceId, {
        git_ref: values.git_ref || undefined,
        version: values.version || undefined,
      });
      const task = await pollTaskUntilDone(accepted.task_id, { timeoutMs: BUILD_POLL_TIMEOUT_MS });
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
      render: (v: string | null) => (v ? <code>{v.slice(0, 12)}</code> : <Muted />),
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
      render: (t: string | null) => (t ? new Date(t).toLocaleString("zh-CN") : <Muted />),
    },
    {
      title: "错误",
      dataIndex: "error",
      key: "error",
      render: (e: string | null) => (e ? <span style={{ color: "#E5484D" }}>{e}</span> : <Muted />),
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
          <Button
            type="primary"
            size="small"
            disabled={!service.build_config}
            onClick={() => setTriggerOpen(true)}
          >
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
            locale={{
              emptyText: service.build_config
                ? "暂无构建记录,点击右上角触发构建"
                : "该服务未配置构建,请先在「服务」列表页编辑构建配置",
            }}
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
        <Form.Item name="git_ref" label="Git 引用" extra="留空则用服务构建配置里的默认分支 / 标签。">
          <Input placeholder="如 main / v1.2.3 / 提交 SHA" />
        </Form.Item>
        <Form.Item name="version" label="版本号" extra="留空则用构建配置默认版本。">
          <Input placeholder="如 1.2.3" />
        </Form.Item>
      </FormModal>
    </div>
  );
}

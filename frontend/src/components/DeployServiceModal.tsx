/**
 * 统一部署入口(块三:部署动线收敛)。
 *
 * 此前部署被劈成两条互不相通的路径:「构建」页产物 Tab 按 artifact_id 部署、
 * 「部署」页按 version 触发 CI。此组件把两者收敛到一处——一个弹窗里二选一:
 *   - 按制品部署:从该服务已产出的制品里选一个(artifact 直发到 runtime)。
 *   - 按版本部署:填版本号走外部 CI 流水线(原 version 路径)。
 * 再选发布策略。prod 开启审批时后端落 pending 审批,此处提示"已进入审批"而非
 * 轮询 task;否则轮询 task 到终态回显。两条路径共用一个 deployService 调用。
 */

import { useEffect, useState } from "react";
import { Alert, Empty, Form, Input, Segmented, Select, Skeleton, message } from "antd";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { listArtifacts } from "@/api/builds";
import {
  type DeploymentStrategy,
  deployService,
  isPendingApproval,
} from "@/api/deployments";
import type { Service } from "@/api/services";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { FormModal } from "@/components/FormModal";

const STRATEGY_OPTIONS: { label: string; value: DeploymentStrategy }[] = [
  { label: "滚动", value: "rolling" },
  { label: "重建", value: "recreate" },
  { label: "金丝雀", value: "canary" },
  { label: "蓝绿", value: "blue-green" },
];

type DeployMode = "artifact" | "version";

interface DeployFormValues {
  mode: DeployMode;
  artifact_id?: string;
  version?: string;
  strategy: DeploymentStrategy;
}

export interface DeployServiceModalProps {
  service: Service | null;
  open: boolean;
  onClose: () => void;
  /** 部署受理并轮询到终态后回调(供父级刷新部署历史)。 */
  onDeployed?: () => void;
}

export function DeployServiceModal({
  service,
  open,
  onClose,
  onDeployed,
}: DeployServiceModalProps): React.ReactElement {
  const [form] = Form.useForm<DeployFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const serviceId = service?.id;

  // 该服务已产出的制品:按制品部署时供选择。弹窗打开且选服务后才拉。
  const { data: artifacts, isLoading: artifactsLoading } = useQuery({
    queryKey: ["artifacts", serviceId],
    queryFn: () => listArtifacts(serviceId as string),
    enabled: open && serviceId != null,
  });

  useEffect(() => {
    if (open) {
      form.setFieldsValue({ mode: "artifact", strategy: "rolling" });
    }
  }, [open, form]);

  const handleFinish = async (values: DeployFormValues): Promise<void> => {
    if (!serviceId) {
      return;
    }
    if (values.mode === "artifact" && !values.artifact_id) {
      message.warning("请选择要部署的制品");
      return;
    }
    if (values.mode === "version" && !values.version?.trim()) {
      message.warning("请输入部署版本");
      return;
    }
    setSubmitting(true);
    const hide = message.loading("部署触发中…", 0);
    try {
      const result = await deployService(serviceId, {
        artifact_id: values.mode === "artifact" ? values.artifact_id : undefined,
        version: values.mode === "version" ? values.version?.trim() : undefined,
        strategy: values.strategy,
      });
      if (isPendingApproval(result)) {
        hide();
        message.info("该操作为生产高危变更,已提交审批,待审批通过后执行");
        onClose();
        form.resetFields();
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
      onClose();
      form.resetFields();
      onDeployed?.();
    } catch (err) {
      hide();
      message.error(err instanceof ApiError ? err.message : "部署请求失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <FormModal<DeployFormValues>
      title={service ? `部署 · ${service.name}` : "部署"}
      open={open}
      form={form}
      onFinish={(v) => void handleFinish(v)}
      onClose={onClose}
      confirmLoading={submitting}
      okText="部署"
    >
      <Form.Item name="mode" label="部署来源" initialValue="artifact">
        <Segmented
          options={[
            { label: "已构建制品", value: "artifact" },
            { label: "指定版本(CI)", value: "version" },
          ]}
        />
      </Form.Item>

      <Form.Item noStyle shouldUpdate={(prev, cur) => prev.mode !== cur.mode}>
        {({ getFieldValue }) => {
          const mode = (getFieldValue("mode") as DeployMode) ?? "artifact";
          if (mode === "version") {
            return (
              <Form.Item
                name="version"
                label="版本"
                rules={[{ required: true, message: "请输入部署版本" }]}
                extra="走外部 CI 流水线部署该版本。"
              >
                <Input placeholder="如 v1.2.3" />
              </Form.Item>
            );
          }
          if (artifactsLoading) {
            return <Skeleton active paragraph={{ rows: 2 }} />;
          }
          if (!artifacts || artifacts.length === 0) {
            return (
              <Alert
                type="info"
                showIcon
                message="该服务暂无已构建制品"
                description="请先到「构建」分区触发一次构建,或改用「指定版本(CI)」部署。"
                style={{ marginBottom: 8 }}
              />
            );
          }
          return (
            <Form.Item
              name="artifact_id"
              label="制品"
              rules={[{ required: true, message: "请选择要部署的制品" }]}
            >
              <Select
                placeholder="选择已构建的制品"
                options={artifacts.map((a) => ({
                  label: `${a.name}${a.version ? ` · ${a.version}` : ""} · ${a.uri}`,
                  value: a.id,
                }))}
                notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
              />
            </Form.Item>
          );
        }}
      </Form.Item>

      <Form.Item
        name="strategy"
        label="发布策略"
        initialValue="rolling"
        extra="k8s 支持 rolling / recreate;canary、蓝绿在裸机(接入负载均衡)可用,k8s 需 Argo Rollouts。"
      >
        <Segmented options={STRATEGY_OPTIONS} />
      </Form.Item>
    </FormModal>
  );
}

/**
 * 配置管理页(设计 §12)。
 *
 * 选一个服务后管理其配置版本:列版本历史(标记生效中)、新建版本(内容+格式+
 * 说明)、切换生效版(配置回滚)。配置与部署解耦,故独立成页(与「部署」页并列)。
 * 敏感值用 ${secret:名称} 引用,不落明文密钥(§12.2)。
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
  Tag,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { diffLines, diffSummary } from "@/api/configDiff";
import { listServices, type Service } from "@/api/services";
import {
  type ConfigDelivery,
  type ConfigFormat,
  type ConfigVersion,
  activateConfigVersion,
  applyConfigVersion,
  createConfigVersion,
  getCurrentConfig,
  listConfigDeliveries,
  listConfigVersions,
} from "@/api/deployments";
import { pollTaskUntilDone } from "@/api/taskPolling";
import { colors } from "@/theme";

const FORMAT_OPTIONS: { label: string; value: ConfigFormat }[] = [
  { label: "env", value: "env" },
  { label: "yaml", value: "yaml" },
  { label: "properties", value: "properties" },
  { label: "json", value: "json" },
];

interface ConfigFormValues {
  content: string;
  format: ConfigFormat;
  comment?: string;
  target_path?: string;
}

function ConfigPanel({ serviceId }: { serviceId: string }): React.ReactElement {
  const queryClient = useQueryClient();
  const [form] = Form.useForm<ConfigFormValues>();
  const [applying, setApplying] = useState<number | null>(null);
  const [deliveryVersion, setDeliveryVersion] = useState<number | null>(null);
  const [diffVersion, setDiffVersion] = useState<number | null>(null);

  const { data: versions, isLoading } = useQuery({
    queryKey: ["configs", serviceId],
    queryFn: () => listConfigVersions(serviceId),
  });
  const { data: current } = useQuery({
    queryKey: ["config-current", serviceId],
    queryFn: () => getCurrentConfig(serviceId),
  });
  const { data: deliveries, isLoading: deliveriesLoading } = useQuery({
    queryKey: ["config-deliveries", serviceId, deliveryVersion],
    queryFn: () => listConfigDeliveries(serviceId, deliveryVersion as number),
    enabled: deliveryVersion !== null,
  });

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ["configs", serviceId] });
    void queryClient.invalidateQueries({ queryKey: ["config-current", serviceId] });
  };

  const createMutation = useMutation({
    mutationFn: (body: ConfigFormValues) => createConfigVersion(serviceId, body),
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

  // 下发:异步落 task,轮询到终态后按结果提示,并刷新该版本的逐目标下发记录。
  const applyVersion = async (version: number): Promise<void> => {
    setApplying(version);
    try {
      const { task_id } = await applyConfigVersion(serviceId, version);
      const task = await pollTaskUntilDone(task_id);
      if (task.status === "success") {
        message.success(`v${version} 已下发到所有目标`);
      } else if (task.status === "failed") {
        message.error(`v${version} 下发未全部成功,查看下发结果`);
      } else {
        message.warning(`v${version} 下发状态未知,请稍后核对`);
      }
      setDeliveryVersion(version);
      void queryClient.invalidateQueries({
        queryKey: ["config-deliveries", serviceId, version],
      });
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "下发失败");
    } finally {
      setApplying(null);
    }
  };

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
    { title: "格式", dataIndex: "format", key: "format", width: 80 },
    {
      title: "下发路径",
      dataIndex: "target_path",
      key: "target_path",
      render: (p: string | null) =>
        p ? (
          <span style={{ fontFamily: "monospace", fontSize: 12 }}>{p}</span>
        ) : (
          <span style={{ color: "#B0B3B5" }}>未设置</span>
        ),
    },
    { title: "修改人", dataIndex: "created_by", key: "created_by", width: 100 },
    {
      title: "操作",
      key: "actions",
      width: 210,
      render: (_, row) => (
        <span style={{ display: "flex", gap: 4 }}>
          {!row.is_current && (
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
          )}
          <Popconfirm
            title={`下发 v${row.version} 到该服务所有目标机?`}
            okText="下发"
            cancelText="取消"
            disabled={!row.target_path}
            onConfirm={() => applyVersion(row.version)}
          >
            <Button
              size="small"
              type="link"
              disabled={!row.target_path}
              loading={applying === row.version}
            >
              下发
            </Button>
          </Popconfirm>
          <Button size="small" type="link" onClick={() => setDeliveryVersion(row.version)}>
            下发结果
          </Button>
          {row.version > 1 && (
            <Button size="small" type="link" onClick={() => setDiffVersion(row.version)}>
              对比上一版
            </Button>
          )}
        </span>
      ),
    },
  ];

  // 与上一版(version-1)对比:两版内容做行 diff,供 Modal 渲染
  const diffData = (() => {
    if (diffVersion === null || !versions) return null;
    const cur = versions.find((v) => v.version === diffVersion);
    const prev = versions.find((v) => v.version === diffVersion - 1);
    if (!cur || !prev) return null;
    const lines = diffLines(prev.content, cur.content);
    return { lines, summary: diffSummary(lines), from: prev.version, to: cur.version };
  })();

  const deliveryColumns: ColumnsType<ConfigDelivery> = [
    { title: "放置点", dataIndex: "placement_id", key: "placement_id", width: 240 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: ConfigDelivery["status"]) => {
        const map = {
          success: { color: colors.success, label: "成功" },
          failed: { color: colors.danger, label: "失败" },
          pending: { color: "#8C8C8C", label: "待下发" },
        } as const;
        const { color, label } = map[s];
        return <Tag color={color}>{label}</Tag>;
      },
    },
    {
      title: "结果 / 错误",
      key: "detail",
      render: (_, row) =>
        row.error ? (
          <span style={{ color: colors.danger }}>{row.error}</span>
        ) : (
          <span style={{ color: colors.textBody }}>{row.result ?? "—"}</span>
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
          <Form.Item
            name="target_path"
            label="下发路径(可选)"
            extra="下发时写到目标机的绝对路径,如 /etc/app/app.env;不填则该版本只能查看,不能下发。"
          >
            <Input placeholder="/etc/app/app.env" />
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

      <Modal
        title={deliveryVersion !== null ? `v${deliveryVersion} 下发结果` : "下发结果"}
        open={deliveryVersion !== null}
        onCancel={() => setDeliveryVersion(null)}
        footer={null}
        width={640}
      >
        {deliveriesLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : (
          <Table<ConfigDelivery>
            rowKey="id"
            size="small"
            columns={deliveryColumns}
            dataSource={deliveries ?? []}
            pagination={false}
            locale={{ emptyText: "该版本尚无下发记录" }}
            bordered
          />
        )}
      </Modal>
      <Modal
        title={
          diffData
            ? `配置对比 v${diffData.from} → v${diffData.to}(+${diffData.summary.added} -${diffData.summary.removed})`
            : "配置对比"
        }
        open={diffVersion !== null}
        onCancel={() => setDiffVersion(null)}
        footer={null}
        width={720}
      >
        {diffData ? (
          <div
            style={{
              fontFamily: "monospace",
              fontSize: 12,
              lineHeight: 1.6,
              maxHeight: 480,
              overflow: "auto",
              border: `1px solid ${colors.cardBorder}`,
              borderRadius: 4,
            }}
          >
            {diffData.lines.map((line, idx) => {
              const style =
                line.kind === "added"
                  ? { background: "#F6FFED", color: "#237804" }
                  : line.kind === "removed"
                    ? { background: "#FFF1F0", color: "#A8071A" }
                    : { color: colors.textBody };
              const prefix =
                line.kind === "added" ? "+ " : line.kind === "removed" ? "- " : "  ";
              return (
                <div key={idx} style={{ ...style, padding: "0 8px", whiteSpace: "pre-wrap" }}>
                  {prefix}
                  {line.text || " "}
                </div>
              );
            })}
          </div>
        ) : (
          <Empty description="无法对比(缺少相邻版本)" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Modal>
    </div>
  );
}

export function ConfigsPage(): React.ReactElement {
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
      <Empty description="暂无服务,先在「服务」页创建" image={Empty.PRESENTED_IMAGE_SIMPLE} />
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: colors.textTitle }}>配置管理</span>
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
      {selected && <ConfigPanel serviceId={selected.id} />}
    </div>
  );
}

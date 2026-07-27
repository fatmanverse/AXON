/**
 * 服务器现状抽屉:展示"这台机器上实际跑着什么"(块一)。
 *
 * 点服务器名打开,实时拉 GET /api/servers/{id}/inventory,分四块呈现:
 * 资源水位(内存/磁盘/负载)、Docker 容器、systemd 服务、监听端口。
 * 各块独立可用性——某项探测失败(未装 docker、命令缺失、无权限)只在该块位置
 * 显示原因,不影响其余块。这是与 StatusCollector 不同的"全量发现",不依赖
 * 控制面是否登记过该服务。
 */

import {
  Alert,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Progress,
  Skeleton,
  Space,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import {
  type ContainerInfo,
  type InventorySection,
  type ListenPortInfo,
  type ResourceSnapshot,
  type Server,
  type SystemdServiceInfo,
  getServerInventory,
} from "@/api/servers";
import { Muted } from "@/components/Muted";
import { colors } from "@/theme";

interface ServerInventoryDrawerProps {
  server: Server | null;
  open: boolean;
  onClose: () => void;
}

// 探测失败时统一的降级提示:不抛错、不空白,如实说明该项为何看不到。
function SectionUnavailable({ section }: { section: InventorySection }): React.ReactElement {
  return (
    <Alert
      type="warning"
      showIcon
      message="该项现状不可读"
      description={section.error ?? "目标机未返回可解析的结果"}
      style={{ marginBottom: 4 }}
    />
  );
}

function formatMb(mb: number | null): string {
  if (mb === null) return "-";
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function formatKb(kb: number | null): string {
  if (kb === null) return "-";
  const mb = kb / 1024;
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(0)} MB`;
}

function usagePercent(used: number | null, total: number | null): number | null {
  if (used === null || total === null || total === 0) return null;
  return Math.round((used / total) * 100);
}

function ResourceBlock({
  resource,
  section,
}: {
  resource: ResourceSnapshot | null;
  section: InventorySection;
}): React.ReactElement {
  if (!section.available) return <SectionUnavailable section={section} />;
  if (!resource) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无资源数据" />;

  const memPct = usagePercent(resource.mem_used_mb, resource.mem_total_mb);
  const diskPct = usagePercent(resource.disk_used_kb, resource.disk_total_kb);

  return (
    <Descriptions column={2} size="small" bordered>
      <Descriptions.Item label="内存">
        {memPct !== null ? (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Progress
              percent={memPct}
              size="small"
              status={memPct > 90 ? "exception" : "normal"}
            />
            <Muted>
              {formatMb(resource.mem_used_mb)} / {formatMb(resource.mem_total_mb)}
            </Muted>
          </Space>
        ) : (
          <Muted />
        )}
      </Descriptions.Item>
      <Descriptions.Item label={`磁盘 ${resource.disk_mount ?? ""}`}>
        {diskPct !== null ? (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Progress
              percent={diskPct}
              size="small"
              status={diskPct > 90 ? "exception" : "normal"}
            />
            <Muted>
              {formatKb(resource.disk_used_kb)} / {formatKb(resource.disk_total_kb)}
            </Muted>
          </Space>
        ) : (
          <Muted />
        )}
      </Descriptions.Item>
      <Descriptions.Item label="负载 (1/5/15 分钟)" span={2}>
        {resource.load1 !== null ? (
          <span style={{ color: colors.textBody }}>
            {resource.load1?.toFixed(2)} / {resource.load5?.toFixed(2)} /{" "}
            {resource.load15?.toFixed(2)}
          </span>
        ) : (
          <Muted />
        )}
      </Descriptions.Item>
    </Descriptions>
  );
}

const containerColumns: ColumnsType<ContainerInfo> = [
  { title: "名称", dataIndex: "name", key: "name" },
  {
    title: "镜像",
    dataIndex: "image",
    key: "image",
    render: (v: string) => <code>{v}</code>,
  },
  {
    title: "状态",
    dataIndex: "state",
    key: "state",
    width: 90,
    render: (state: string, row) => (
      <Tag color={state === "running" ? colors.success : colors.neutral}>
        {state || row.status}
      </Tag>
    ),
  },
  {
    title: "端口",
    dataIndex: "ports",
    key: "ports",
    render: (v: string) => (v ? <code>{v}</code> : <Muted />),
  },
];

const serviceColumns: ColumnsType<SystemdServiceInfo> = [
  { title: "单元", dataIndex: "unit", key: "unit" },
  {
    title: "状态",
    dataIndex: "sub",
    key: "sub",
    width: 100,
    render: (sub: string) => (
      <Tag color={sub === "running" ? colors.success : colors.neutral}>{sub}</Tag>
    ),
  },
  {
    title: "描述",
    dataIndex: "description",
    key: "description",
    render: (v: string) => v || <Muted />,
  },
];

const portColumns: ColumnsType<ListenPortInfo> = [
  {
    title: "协议",
    dataIndex: "protocol",
    key: "protocol",
    width: 70,
    render: (v: string) => <Tag>{v}</Tag>,
  },
  { title: "地址", dataIndex: "address", key: "address", render: (v: string) => <code>{v}</code> },
  { title: "端口", dataIndex: "port", key: "port", width: 80 },
  {
    title: "进程",
    dataIndex: "process",
    key: "process",
    render: (v: string) => v || <Muted />,
  },
];

export function ServerInventoryDrawer({
  server,
  open,
  onClose,
}: ServerInventoryDrawerProps): React.ReactElement {
  const { data, isLoading, error } = useQuery({
    queryKey: ["server-inventory", server?.id],
    queryFn: () => getServerInventory(server!.id),
    enabled: open && server !== null,
  });

  return (
    <Drawer
      title={server ? `${server.name} · 当前运行` : "当前运行"}
      open={open}
      onClose={onClose}
      width={720}
      destroyOnHidden
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message="读取现状失败"
          description={error instanceof ApiError ? error.message : "请稍后重试"}
        />
      ) : isLoading || !data ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Card size="small" title="资源水位">
            <ResourceBlock resource={data.resource} section={data.resource_section} />
          </Card>
          <Card size="small" title={`Docker 容器 (${data.containers.length})`}>
            {data.containers_section.available ? (
              <Table<ContainerInfo>
                rowKey="name"
                size="small"
                columns={containerColumns}
                dataSource={data.containers}
                pagination={false}
                locale={{ emptyText: "无运行中的容器" }}
              />
            ) : (
              <SectionUnavailable section={data.containers_section} />
            )}
          </Card>
          <Card size="small" title={`systemd 服务 (${data.services.length})`}>
            {data.services_section.available ? (
              <Table<SystemdServiceInfo>
                rowKey="unit"
                size="small"
                columns={serviceColumns}
                dataSource={data.services}
                pagination={false}
                locale={{ emptyText: "无运行中的 service 单元" }}
              />
            ) : (
              <SectionUnavailable section={data.services_section} />
            )}
          </Card>
          <Card size="small" title={`监听端口 (${data.ports.length})`}>
            {data.ports_section.available ? (
              <Table<ListenPortInfo>
                rowKey={(r) => `${r.protocol}-${r.address}-${r.port}`}
                size="small"
                columns={portColumns}
                dataSource={data.ports}
                pagination={false}
                locale={{ emptyText: "无监听端口" }}
              />
            ) : (
              <SectionUnavailable section={data.ports_section} />
            )}
          </Card>
        </Space>
      )}
    </Drawer>
  );
}

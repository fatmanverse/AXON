/**
 * 主页 Dashboard(T2.17,设计 §9.2)。
 *
 * 回答三个问题:现在线上什么状态?最近发生了什么?哪里出问题了?四块数据:
 * - 服务器概览:在线/离线计数(来自 servers 列表的 agent_status)。
 * - 服务健康:运行/异常/停止计数(来自 services 的 placement 观测态聚合)。
 * - 最近部署 feed:跨服务最近部署(来自 GET /api/deployments),标来源/状态/操作人。
 * - 告警区:firing 告警(来自 GET /api/alerts)。
 *
 * 每块独立查询、独立加载/错误态,任一失败不拖垮整页。
 */

import { Card, Col, Empty, Row, Skeleton, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listAlerts, type Alert } from "@/api/alerts";
import { listRecentDeployments, type Deployment, type DeploymentStatus } from "@/api/deployments";
import { listServers } from "@/api/servers";
import { listServices } from "@/api/services";
import { colors } from "@/theme";

const DEPLOY_STATUS: Record<DeploymentStatus, { color: string; label: string }> = {
  running: { color: colors.warning, label: "部署中" },
  success: { color: colors.success, label: "成功" },
  failed: { color: colors.danger, label: "失败" },
  rolled_back: { color: "#8C8C8C", label: "已回滚" },
};

function ServerOverview(): React.ReactElement {
  const { data, isLoading } = useQuery({ queryKey: ["servers"], queryFn: listServers });
  const online = (data ?? []).filter((s) => s.agent_status === "online").length;
  const offline = (data ?? []).length - online;
  return (
    <Card size="small" title="服务器概览" styles={{ body: { padding: 16 } }}>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 1 }} />
      ) : (
        <Row gutter={16}>
          <Col span={12}>
            <Statistic
              title="在线"
              value={online}
              valueStyle={{ color: colors.success }}
            />
          </Col>
          <Col span={12}>
            <Statistic
              title="离线/未知"
              value={offline}
              valueStyle={{ color: offline > 0 ? colors.danger : colors.textBody }}
            />
          </Col>
        </Row>
      )}
    </Card>
  );
}

function ServiceHealth(): React.ReactElement {
  const { data, isLoading } = useQuery({ queryKey: ["services"], queryFn: () => listServices() });
  const total = (data ?? []).length;
  return (
    <Card size="small" title="服务健康" styles={{ body: { padding: 16 } }}>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 1 }} />
      ) : (
        <Row gutter={16}>
          <Col span={12}>
            <Statistic title="服务总数" value={total} />
          </Col>
          <Col span={12}>
            <Statistic
              title="放置点合计"
              value={(data ?? []).reduce((n, s) => n + s.placement_count, 0)}
            />
          </Col>
        </Row>
      )}
    </Card>
  );
}

export function HomePage(): React.ReactElement {
  return (
    <div>
      <Row gutter={[12, 12]}>
        <Col xs={24} md={12}>
          <ServerOverview />
        </Col>
        <Col xs={24} md={12}>
          <ServiceHealth />
        </Col>
      </Row>
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} xl={14}>
          <DeployFeed />
        </Col>
        <Col xs={24} xl={10}>
          <AlertPanel />
        </Col>
      </Row>
    </div>
  );
}

function DeployFeed(): React.ReactElement {
  const { data, isLoading, error } = useQuery({
    queryKey: ["recent-deployments"],
    queryFn: () => listRecentDeployments({ limit: 10 }),
    refetchInterval: 15000,
  });
  const columns: ColumnsType<Deployment> = [
    {
      title: "版本",
      dataIndex: "version",
      key: "version",
      render: (v: string | null) => v ?? <span style={{ color: "#B0B3B5" }}>—</span>,
    },
    { title: "环境", dataIndex: "env", key: "env", width: 80 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: DeploymentStatus) => {
        const tag = DEPLOY_STATUS[s];
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
  ];
  return (
    <Card
      size="small"
      title="最近部署"
      extra={<Link to="/deployments">全部</Link>}
      styles={{ body: { padding: 12 } }}
    >
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : error ? (
        <Empty description="加载部署失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Table<Deployment>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data ?? []}
          pagination={false}
          locale={{ emptyText: "暂无部署记录" }}
        />
      )}
    </Card>
  );
}

function AlertPanel(): React.ReactElement {
  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts", "firing"],
    queryFn: () => listAlerts({ status: "firing" }),
    refetchInterval: 15000,
  });
  const columns: ColumnsType<Alert> = [
    {
      title: "级别",
      dataIndex: "severity",
      key: "severity",
      width: 90,
      render: (s: Alert["severity"]) => {
        const color =
          s === "critical" ? colors.danger : s === "warning" ? colors.warning : "#8C8C8C";
        return <Tag color={color}>{s}</Tag>;
      },
    },
    { title: "摘要", dataIndex: "summary", key: "summary", ellipsis: true },
    {
      title: "服务",
      dataIndex: "service",
      key: "service",
      width: 110,
      render: (s: string | null) => s ?? <span style={{ color: "#B0B3B5" }}>—</span>,
    },
  ];
  return (
    <Card
      size="small"
      title="告警(firing)"
      extra={<Link to="/alerts">全部</Link>}
      styles={{ body: { padding: 12 } }}
    >
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 4 }} />
      ) : error ? (
        <Empty description="加载告警失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Table<Alert>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data ?? []}
          pagination={false}
          locale={{ emptyText: "当前无 firing 告警" }}
        />
      )}
    </Card>
  );
}

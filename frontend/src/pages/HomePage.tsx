/**
 * 主页 Dashboard(T2.17,设计 §9.2)。
 *
 * 回答三个问题:现在线上什么状态?最近发生了什么?哪里出问题了?
 * - 顶部 KPI 条:服务器在线/离线、服务总数/放置点、firing 告警数(带图标徽章的统计瓦片)。
 * - 最近部署 feed:跨服务最近部署(GET /api/deployments),标来源/状态/操作人。
 * - 告警区:firing 告警(GET /api/alerts)。
 *
 * 每块独立查询、独立加载/错误态,任一失败不拖垮整页。
 */

import {
  AlertOutlined,
  ApiOutlined,
  CloudServerOutlined,
  DeploymentUnitOutlined,
} from "@ant-design/icons";
import { Card, Col, Empty, Row, Skeleton, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listAlerts, type Alert } from "@/api/alerts";
import { listRecentDeployments, type Deployment, type DeploymentStatus } from "@/api/deployments";
import { listServers } from "@/api/servers";
import { listServices } from "@/api/services";
import { Muted } from "@/components/Muted";
import { ALERT_SEVERITY, DEPLOYMENT_STATUS } from "@/constants/status";
import { colors, shadows } from "@/theme";

/**
 * KPI 统计瓦片:左侧色带 + 图标徽章 + 大号数字 + 副标注。
 * 现代后台仪表盘的标准形态,替代朴素 Statistic 的"标题 + 数字"。
 */
interface StatTileProps {
  icon: React.ReactNode;
  accent: string;
  label: string;
  value: number | string;
  suffix?: React.ReactNode;
  loading?: boolean;
  to?: string;
}

function StatTile({
  icon,
  accent,
  label,
  value,
  suffix,
  loading,
  to,
}: StatTileProps): React.ReactElement {
  const body = (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "18px 20px",
        background: colors.cardBg,
        border: `1px solid ${colors.cardBorder}`,
        borderRadius: 10,
        boxShadow: shadows.card,
        overflow: "hidden",
        transition: "box-shadow .2s ease, transform .2s ease",
      }}
    >
      {/* 左侧强调色带:仅一条细边,克制不铺满 */}
      <span
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 3,
          background: accent,
        }}
      />
      {/* 图标徽章:淡色底 + 主色图标,呼应强调色但不刺眼 */}
      <div
        style={{
          flex: "none",
          width: 44,
          height: 44,
          borderRadius: 10,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 20,
          color: accent,
          background: `${accent}14`,
        }}
      >
        {icon}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, color: colors.textMuted, marginBottom: 2 }}>{label}</div>
        {loading ? (
          <Skeleton.Button active size="small" style={{ width: 60, height: 28 }} />
        ) : (
          <div
            style={{
              fontSize: 26,
              fontWeight: 600,
              lineHeight: 1.1,
              color: colors.textTitle,
              display: "flex",
              alignItems: "baseline",
              gap: 6,
            }}
          >
            {value}
            {suffix != null && (
              <span style={{ fontSize: 13, fontWeight: 400, color: colors.textMuted }}>
                {suffix}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );

  return to ? (
    <Link to={to} style={{ display: "block" }}>
      {body}
    </Link>
  ) : (
    body
  );
}

function KpiRow(): React.ReactElement {
  const servers = useQuery({ queryKey: ["servers"], queryFn: listServers });
  const services = useQuery({ queryKey: ["services"], queryFn: () => listServices() });
  const firing = useQuery({
    queryKey: ["alerts", "firing"],
    queryFn: () => listAlerts({ status: "firing" }),
    refetchInterval: 30_000,
  });

  const online = (servers.data ?? []).filter((s) => s.agent_status === "online").length;
  const offline = (servers.data ?? []).length - online;
  const serviceTotal = (services.data ?? []).length;
  const placements = (services.data ?? []).reduce((n, s) => n + s.placement_count, 0);
  const firingCount = (firing.data ?? []).length;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={12} md={6}>
        <StatTile
          to="/servers"
          icon={<CloudServerOutlined />}
          accent={colors.success}
          label="在线服务器"
          value={online}
          suffix={offline > 0 ? `/ ${offline} 离线` : undefined}
          loading={servers.isLoading}
        />
      </Col>
      <Col xs={12} md={6}>
        <StatTile
          to="/services"
          icon={<ApiOutlined />}
          accent={colors.info}
          label="纳管服务"
          value={serviceTotal}
          loading={services.isLoading}
        />
      </Col>
      <Col xs={12} md={6}>
        <StatTile
          to="/services"
          icon={<DeploymentUnitOutlined />}
          accent={colors.accentViolet}
          label="放置点合计"
          value={placements}
          loading={services.isLoading}
        />
      </Col>
      <Col xs={12} md={6}>
        <StatTile
          to="/alerts"
          icon={<AlertOutlined />}
          accent={firingCount > 0 ? colors.danger : colors.textMuted}
          label="触发中告警"
          value={firingCount}
          loading={firing.isLoading}
        />
      </Col>
    </Row>
  );
}

export function HomePage(): React.ReactElement {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <KpiRow />
      <Row gutter={[16, 16]}>
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
      render: (v: string | null) => v ?? <Muted />,
    },
    { title: "环境", dataIndex: "env", key: "env", width: 80 },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: DeploymentStatus) => {
        const meta = DEPLOYMENT_STATUS[s];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    { title: "来源", dataIndex: "source", key: "source", width: 130 },
    {
      title: "操作人",
      dataIndex: "operator",
      key: "operator",
      render: (o: string | null) => o ?? <Muted />,
    },
  ];
  return (
    <Card
      title="最近部署"
      extra={<Link to="/deployments">全部</Link>}
      styles={{ body: { padding: 0 } }}
    >
      {isLoading ? (
        <div style={{ padding: 16 }}>
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      ) : error ? (
        <Empty
          description="加载部署失败"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: 24 }}
        />
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
        const meta = ALERT_SEVERITY[s];
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    { title: "摘要", dataIndex: "summary", key: "summary", ellipsis: true },
    {
      title: "服务",
      dataIndex: "service",
      key: "service",
      width: 110,
      render: (s: string | null) => s ?? <Muted />,
    },
  ];
  return (
    <Card
      title="告警(firing)"
      extra={<Link to="/alerts">全部</Link>}
      styles={{ body: { padding: 0 } }}
    >
      {isLoading ? (
        <div style={{ padding: 16 }}>
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      ) : error ? (
        <Empty
          description="加载告警失败"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          style={{ padding: 24 }}
        />
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

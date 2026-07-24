/**
 * 告警页(T3.7,设计 §6.3/§9.2)。
 *
 * 列出 Alertmanager 回流的告警(严重级别/摘要/服务/状态/时间),支持按状态过滤
 * (全部/firing/resolved)。入库由 webhook 完成,本页只读。
 */

import { useState } from "react";
import { Card, Result, Segmented, Skeleton, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { type Alert, type AlertSeverity, type AlertStatus, listAlerts } from "@/api/alerts";
import { Muted } from "@/components/Muted";
import { PageHeader } from "@/components/PageHeader";
import { ALERT_SEVERITY, ALERT_STATUS } from "@/constants/status";
import { colors, shadows } from "@/theme";

type StatusFilter = "all" | AlertStatus;

const FILTER_OPTIONS: { label: string; value: StatusFilter }[] = [
  { label: "全部", value: "all" },
  { label: "触发中", value: "firing" },
  { label: "已恢复", value: "resolved" },
];

export function AlertsPage(): React.ReactElement {
  const [filter, setFilter] = useState<StatusFilter>("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["alerts", filter],
    queryFn: () => listAlerts(filter === "all" ? undefined : { status: filter }),
  });

  const columns: ColumnsType<Alert> = [
    {
      title: "级别",
      dataIndex: "severity",
      key: "severity",
      width: 80,
      render: (s: AlertSeverity) => {
        const conf = ALERT_SEVERITY[s];
        return <Tag color={conf.color}>{conf.label}</Tag>;
      },
    },
    {
      title: "摘要",
      dataIndex: "summary",
      key: "summary",
      render: (v: string) => <span style={{ color: colors.textTitle }}>{v}</span>,
    },
    {
      title: "服务",
      dataIndex: "service",
      key: "service",
      width: 140,
      render: (v: string | null) => v ?? <Muted />,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: AlertStatus) => {
        const conf = ALERT_STATUS[s];
        return <Tag color={conf.color}>{conf.label}</Tag>;
      },
    },
    {
      title: "触发时间",
      dataIndex: "fired_at",
      key: "fired_at",
      width: 180,
      render: (t: string | null) => (t ? new Date(t).toLocaleString("zh-CN") : <Muted />),
    },
  ];

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载告警失败"}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="告警"
        extra={
          <Segmented
            size="small"
            value={filter}
            onChange={(v) => setFilter(v as StatusFilter)}
            options={FILTER_OPTIONS}
          />
        }
      />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 5 }} />
      ) : (
        <Card styles={{ body: { padding: 0 } }} style={{ boxShadow: shadows.card }}>
          <Table<Alert>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={data ?? []}
            pagination={false}
            locale={{ emptyText: "暂无告警" }}
          />
        </Card>
      )}
    </div>
  );
}

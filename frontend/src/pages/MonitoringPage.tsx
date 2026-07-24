/**
 * 资源监控大盘页(T1.18,设计 §6.2 / §15.4)。
 *
 * 选一台服务器 + 时间范围,经 /api/metrics/query_range 拉 node_exporter 指标,
 * 用 ECharts 画 CPU/内存/磁盘/负载四张曲线卡。控制面屏蔽 Prometheus 直连,
 * 前端只认统一的 metrics 端点(§6.2)。指标按 instance=host:9100 过滤到选中机。
 *
 * 无纳管服务器时给引导空态;查询失败逐卡显示错误,不整页崩。
 */

import { useMemo, useState } from "react";
import { Card, Col, Empty, Result, Row, Segmented, Select, Skeleton } from "antd";
import { useQueries, useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { listServers } from "@/api/servers";
import { listServices } from "@/api/services";
import { listDeployments } from "@/api/deployments";
import { queryRange } from "@/api/metrics";
import { matrixToSeries } from "@/api/metricsTransform";
import { ResourceChart, type DeployMarker } from "@/components/ResourceChart";
import { PageHeader } from "@/components/PageHeader";

const NODE_EXPORTER_PORT = 9100;

// 时间窗选项:label 给人看,seconds 用于算 start,step 控制点密度。
const RANGE_OPTIONS = [
  { label: "近 30 分钟", value: "30m", seconds: 30 * 60, step: 30 },
  { label: "近 1 小时", value: "1h", seconds: 60 * 60, step: 60 },
  { label: "近 6 小时", value: "6h", seconds: 6 * 60 * 60, step: 300 },
  { label: "近 24 小时", value: "24h", seconds: 24 * 60 * 60, step: 900 },
] as const;

type RangeValue = (typeof RANGE_OPTIONS)[number]["value"];

interface ChartSpec {
  key: string;
  title: string;
  unit: string;
  /** 用 %s 占位 instance,拼出针对选中机的 PromQL。 */
  promql: (instance: string) => string;
}

// node_exporter 标准指标的 PromQL:按 instance 过滤到选中机。
const CHART_SPECS: ChartSpec[] = [
  {
    key: "cpu",
    title: "CPU 使用率",
    unit: "%",
    promql: (i) =>
      `100 - (avg by (instance)(rate(node_cpu_seconds_total{mode="idle",instance="${i}"}[5m])) * 100)`,
  },
  {
    key: "memory",
    title: "内存使用率",
    unit: "%",
    promql: (i) =>
      `(1 - node_memory_MemAvailable_bytes{instance="${i}"} / node_memory_MemTotal_bytes{instance="${i}"}) * 100`,
  },
  {
    key: "disk",
    title: "根分区磁盘使用率",
    unit: "%",
    promql: (i) =>
      `(1 - node_filesystem_avail_bytes{instance="${i}",mountpoint="/"} / node_filesystem_size_bytes{instance="${i}",mountpoint="/"}) * 100`,
  },
  {
    key: "load",
    title: "系统负载(1m)",
    unit: "",
    promql: (i) => `node_load1{instance="${i}"}`,
  },
];

export function MonitoringPage(): React.ReactElement {
  const [range, setRange] = useState<RangeValue>("1h");
  const [serverId, setServerId] = useState<string | undefined>();
  // 部署标注(§9.2 运维最爱):可选叠加某服务的部署时间点竖线到曲线上
  const [markerServiceId, setMarkerServiceId] = useState<string | undefined>();

  const {
    data: servers,
    isLoading: serversLoading,
    error: serversError,
  } = useQuery({ queryKey: ["servers"], queryFn: listServers });

  const { data: services } = useQuery({ queryKey: ["services"], queryFn: () => listServices() });

  // 选中服务的部署历史 → 竖线标注(取成功/进行中的 started_at 打点)
  const { data: markerDeployments } = useQuery({
    queryKey: ["deployments", markerServiceId],
    queryFn: () => listDeployments(markerServiceId as string),
    enabled: Boolean(markerServiceId),
  });

  const markers: DeployMarker[] = useMemo(() => {
    const end = Math.floor(Date.now() / 1000);
    const rangeSpec = RANGE_OPTIONS.find((r) => r.value === range) ?? RANGE_OPTIONS[1];
    const startMs = (end - rangeSpec.seconds) * 1000;
    return (
      (markerDeployments ?? [])
        .filter((d) => d.started_at)
        .map((d) => ({ t: new Date(d.started_at as string).getTime(), label: d.version ?? "部署" }))
        // 只保留落在当前时间窗内的标注,避免窗外竖线挤在边缘
        .filter((m) => m.t >= startMs && m.t <= end * 1000)
    );
  }, [markerDeployments, range]);

  const selected = useMemo(
    () => servers?.find((s) => s.id === serverId) ?? servers?.[0],
    [servers, serverId],
  );
  const instance = selected ? `${selected.host}:${NODE_EXPORTER_PORT}` : null;

  const rangeSpec = RANGE_OPTIONS.find((r) => r.value === range) ?? RANGE_OPTIONS[1];

  const chartQueries = useQueries({
    queries: CHART_SPECS.map((spec) => ({
      queryKey: ["metrics", spec.key, instance, range],
      enabled: Boolean(instance),
      queryFn: async () => {
        const end = Math.floor(Date.now() / 1000);
        const start = end - rangeSpec.seconds;
        const data = await queryRange({
          query: spec.promql(instance as string),
          start,
          end,
          step: rangeSpec.step,
        });
        return matrixToSeries(data);
      },
    })),
  });

  if (serversError) {
    return (
      <Result
        status="warning"
        subTitle={serversError instanceof ApiError ? serversError.message : "加载服务器列表失败"}
      />
    );
  }

  if (serversLoading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  if (!servers || servers.length === 0) {
    return (
      <Empty
        description="暂无纳管服务器,先在「服务器」页纳管并自举 node_exporter"
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="资源监控"
        inline={
          <>
            <Select
              size="small"
              value={selected?.id}
              onChange={setServerId}
              style={{ width: 200 }}
              options={servers.map((s) => ({ label: `${s.name}（${s.host}）`, value: s.id }))}
            />
            <Select
              size="small"
              allowClear
              placeholder="叠加部署标注（选服务）"
              value={markerServiceId}
              onChange={setMarkerServiceId}
              style={{ width: 200 }}
              options={(services ?? []).map((s) => ({
                label: `${s.name}（${s.env}）`,
                value: s.id,
              }))}
            />
          </>
        }
        extra={
          <Segmented
            size="small"
            value={range}
            onChange={(v) => setRange(v as RangeValue)}
            options={RANGE_OPTIONS.map((r) => ({ label: r.label, value: r.value }))}
          />
        }
      />

      <Row gutter={[12, 12]}>
        {CHART_SPECS.map((spec, idx) => {
          const q = chartQueries[idx];
          return (
            <Col xs={24} xl={12} key={spec.key}>
              <Card size="small" variant="outlined" styles={{ body: { padding: 12 } }}>
                <ResourceChart
                  title={spec.title}
                  unit={spec.unit}
                  series={q.data ?? []}
                  markers={markers}
                  loading={q.isLoading}
                  error={
                    q.error instanceof ApiError ? q.error.message : q.error ? "指标查询失败" : null
                  }
                />
              </Card>
            </Col>
          );
        })}
      </Row>
    </div>
  );
}

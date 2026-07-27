/**
 * 服务详情页(块三:部署动线收敛的动线主轴)。
 *
 * 此前一个服务的能力被横切进多个顶级页面:构建在「构建」页、部署在「部署」页、
 * 配置在「配置」页、现状无处可见,且部署入口还分裂成两套(构建产物 Tab vs 部署页)。
 * 本页把「一个服务从构建到上线到观测」串进一个页面的分区里,作为单服务全生命周期
 * 的操作主轴;顶级的构建/部署/配置页保留为「跨服务全局列表」视角。
 *
 * 分区(Segmented):
 *   概览   —— 服务基本信息 + 构建配置摘要 + 放置数,一眼看清服务定义。
 *   构建   —— 构建历史 + 一键触发本地构建(BuildHistorySection)。
 *   部署   —— 部署历史 + 统一部署入口(制品/版本二选一)+ 定向回滚(DeployHistorySection)。
 *   现状   —— 该服务放置所在服务器的实时运行清单入口(块一 inventory 复用)。
 *
 * 单服务数据经 getService 独立拉取,可随操作后失效重取,不依赖列表页。
 */

import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Card, Descriptions, Result, Segmented, Skeleton, Tag } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import { getService } from "@/api/services";
import { PageHeader } from "@/components/PageHeader";
import { Muted } from "@/components/Muted";
import { DeployServiceModal } from "@/components/DeployServiceModal";
import { BuildHistorySection } from "@/pages/serviceDetail/BuildHistorySection";
import { DeploySection } from "@/pages/serviceDetail/DeploySection";
import { colors, shadows } from "@/theme";

type DetailTab = "overview" | "builds" | "deploys";

function OverviewSection({
  service,
}: {
  service: NonNullable<ReturnType<typeof useServiceQuery>["data"]>;
}): React.ReactElement {
  const cfg = service.build_config;
  return (
    <Card style={{ boxShadow: shadows.card }}>
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="服务名">{service.name}</Descriptions.Item>
        <Descriptions.Item label="环境">
          <Tag>{service.env}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="运行时">
          <Tag>{service.runtime}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="放置数">{service.placement_count}</Descriptions.Item>
        <Descriptions.Item label="期望版本">
          {service.desired_version ?? <Muted />}
        </Descriptions.Item>
        <Descriptions.Item label="构建配置">
          {cfg ? (
            <span style={{ color: colors.textBody }}>
              {cfg.artifact_type === "docker" ? "Docker 镜像" : "通用 tar 包"} · {cfg.repo_url}
            </span>
          ) : (
            <Muted>未配置(可在「服务」列表页编辑构建)</Muted>
          )}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
}

function useServiceQuery(serviceId: string | undefined) {
  return useQuery({
    queryKey: ["service", serviceId],
    queryFn: () => getService(serviceId as string),
    enabled: serviceId != null,
  });
}

export function ServiceDetailPage(): React.ReactElement {
  const { serviceId } = useParams<{ serviceId: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<DetailTab>("overview");
  const [deployOpen, setDeployOpen] = useState(false);

  const { data: service, isLoading, error, refetch } = useServiceQuery(serviceId);

  const options = useMemo(
    () => [
      { label: "概览", value: "overview" as const },
      { label: "构建", value: "builds" as const },
      { label: "部署", value: "deploys" as const },
    ],
    [],
  );

  if (error) {
    return (
      <Result
        status="warning"
        subTitle={error instanceof ApiError ? error.message : "加载服务详情失败"}
        extra={
          <Button type="primary" onClick={() => navigate("/services")}>
            返回服务列表
          </Button>
        }
      />
    );
  }

  if (isLoading || !service) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  return (
    <div>
      <PageHeader
        title={
          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <Button
              type="text"
              size="small"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate("/services")}
            />
            {service.name}
          </span>
        }
        inline={<Tag>{service.env}</Tag>}
        extra={
          <Button type="primary" onClick={() => setDeployOpen(true)}>
            部署
          </Button>
        }
      />

      <Segmented<DetailTab>
        value={tab}
        onChange={setTab}
        options={options}
        style={{ marginBottom: 16 }}
      />

      {tab === "overview" && <OverviewSection service={service} />}
      {tab === "builds" && <BuildHistorySection service={service} />}
      {tab === "deploys" && <DeploySection service={service} />}

      <DeployServiceModal
        service={service}
        open={deployOpen}
        onClose={() => setDeployOpen(false)}
        onDeployed={() => void refetch()}
      />
    </div>
  );
}

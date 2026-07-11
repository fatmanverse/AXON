/**
 * 联调占位页:各业务模块页面(服务器/服务/部署等)接入前的占位。
 * 用 /api/auth/me 打通"登录态 + 鉴权头 + envelope 解包"整条链路,
 * 便于在真实页面就绪前验证前后端联调。后续 Epic 逐页替换。
 */

import { Card, Descriptions, Empty, Result, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";

import { fetchMe } from "@/api/auth";
import { ApiError } from "@/api/client";

interface PlaceholderPageProps {
  title: string;
}

export function PlaceholderPage({ title }: PlaceholderPageProps): React.ReactElement {
  const { data, isLoading, error } = useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
  });

  return (
    <Card
      title={title}
      size="small"
      styles={{ header: { fontSize: 14, fontWeight: 600, color: "#333333" } }}
    >
      {isLoading && <Skeleton active paragraph={{ rows: 3 }} />}
      {error && (
        <Result
          status="warning"
          subTitle={
            error instanceof ApiError ? error.message : "加载失败,请稍后重试"
          }
        />
      )}
      {data && (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="当前用户">{data.username}</Descriptions.Item>
          <Descriptions.Item label="角色">
            {data.roles.length ? data.roles.join("、") : <Empty description="无" />}
          </Descriptions.Item>
          <Descriptions.Item label="权限点">
            {data.permissions.length ? (
              data.permissions.join("、")
            ) : (
              <Empty description="无" />
            )}
          </Descriptions.Item>
        </Descriptions>
      )}
    </Card>
  );
}

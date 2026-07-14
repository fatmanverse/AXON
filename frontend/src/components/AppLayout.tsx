/**
 * 布局壳:JumpServer 风格深色侧边导航(分组菜单)+ 顶栏(面包屑 / 用户菜单)+ 内容区。
 * 侧栏按业务域分组(概览 / 资源 / 交付 / 观测),对齐 JumpServer 的分区导航习惯;
 * 顶栏只保留面包屑与用户菜单——环境不在此处切换,各页自带筛选。
 */

import { useMemo, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  BellOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  FileTextOutlined,
  LineChartOutlined,
  LogoutOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Badge, Breadcrumb, Dropdown, Layout, Menu, Tooltip } from "antd";
import type { MenuProps } from "antd";
import { useQuery } from "@tanstack/react-query";

import { listAlerts } from "@/api/alerts";
import { useAuthStore } from "@/stores/auth";
import { colors } from "@/theme";
import logoSidebar from "@/assets/logo-sidebar.svg";
import logoIcon from "@/assets/logo-icon.svg";

const { Sider, Header, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
}

interface NavGroup {
  title: string;
  items: NavItem[];
}

// JumpServer 式分组导航:每组一个小节标题,组内是页面项。
const NAV_GROUPS: NavGroup[] = [
  {
    title: "概览",
    items: [{ key: "/", label: "主页", icon: <DashboardOutlined /> }],
  },
  {
    title: "资源",
    items: [
      { key: "/servers", label: "服务器", icon: <CloudServerOutlined /> },
      { key: "/services", label: "服务", icon: <ApiOutlined /> },
    ],
  },
  {
    title: "交付",
    items: [
      { key: "/deployments", label: "部署", icon: <DeploymentUnitOutlined /> },
      { key: "/approvals", label: "审批", icon: <AuditOutlined /> },
      { key: "/configs", label: "配置", icon: <FileTextOutlined /> },
    ],
  },
  {
    title: "观测",
    items: [
      { key: "/monitoring", label: "监控", icon: <LineChartOutlined /> },
      { key: "/alerts", label: "告警", icon: <AlertOutlined /> },
    ],
  },
];

const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

export function AppLayout(): React.ReactElement {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  // 顶栏告警铃铛:复用 firing 告警(与主页告警区同源),定时轮询刷新未处理数。
  const { data: firingAlerts } = useQuery({
    queryKey: ["alerts", "firing"],
    queryFn: () => listAlerts({ status: "firing" }),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  const firingCount = (firingAlerts ?? []).length;

  const selectedKey = useMemo(() => {
    const path = location.pathname;
    // 先按非根路径做最长前缀匹配,避免 "/" 命中所有路径而恒高亮主页
    const match = NAV_ITEMS.filter((item) => item.key !== "/").find(
      (item) => path === item.key || path.startsWith(`${item.key}/`),
    );
    if (match) {
      return match.key;
    }
    return path === "/" ? "/" : "/servers";
  }, [location.pathname]);

  const currentGroup = NAV_GROUPS.find((group) =>
    group.items.some((item) => item.key === selectedKey),
  );
  const currentLabel = NAV_ITEMS.find((item) => item.key === selectedKey)?.label ?? "";

  // 折叠时收成扁平菜单(组标题占位在窄栏里无意义),展开时用分组菜单。
  const menuItems: MenuProps["items"] = collapsed
    ? NAV_ITEMS.map((item) => ({
        key: item.key,
        icon: item.icon,
        label: <Link to={item.key}>{item.label}</Link>,
      }))
    : NAV_GROUPS.map((group) => ({
        type: "group" as const,
        key: `group:${group.title}`,
        label: group.title,
        children: group.items.map((item) => ({
          key: item.key,
          icon: item.icon,
          label: <Link to={item.key}>{item.label}</Link>,
        })),
      }));

  const handleLogout = (): void => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        theme="dark"
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        width={210}
        style={{ background: colors.sidebarBg }}
      >
        <div
          style={{
            height: 50,
            display: "flex",
            alignItems: "center",
            paddingLeft: collapsed ? 0 : 16,
            justifyContent: collapsed ? "center" : "flex-start",
            borderBottom: `1px solid ${colors.sidebarActiveBg}`,
          }}
        >
          <img
            src={collapsed ? logoIcon : logoSidebar}
            alt="一脉 Axon"
            style={{ height: collapsed ? 24 : 26, color: colors.sidebarText }}
          />
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          style={{ background: "transparent", borderRight: 0 }}
          items={menuItems}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: colors.cardBg,
            borderBottom: `1px solid ${colors.headerBorder}`,
            padding: "0 20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 50,
          }}
        >
          <Breadcrumb
            items={[
              { title: "控制面" },
              ...(currentGroup ? [{ title: currentGroup.title }] : []),
              { title: currentLabel },
            ]}
            style={{ fontSize: 13 }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <Tooltip title={firingCount > 0 ? `${firingCount} 条告警触发中` : "无触发中告警"}>
              <Badge count={firingCount} size="small" offset={[-2, 2]}>
                <BellOutlined
                  onClick={() => navigate("/alerts")}
                  style={{
                    cursor: "pointer",
                    fontSize: 16,
                    color: firingCount > 0 ? colors.danger : colors.textBody,
                  }}
                />
              </Badge>
            </Tooltip>
            <Dropdown
              menu={{
                items: [
                  {
                    key: "logout",
                    icon: <LogoutOutlined />,
                    label: "退出登录",
                    onClick: handleLogout,
                  },
                ],
              }}
            >
              <span
                style={{
                  cursor: "pointer",
                  color: colors.textBody,
                  fontSize: 13,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <UserOutlined />
                {user?.username ?? "未登录"}
              </span>
            </Dropdown>
          </div>
        </Header>
        <Content style={{ background: colors.contentBg, padding: 20 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

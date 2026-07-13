/**
 * 布局壳:深色侧边导航 + 顶栏(面包屑 / 环境切换 / 用户菜单)+ 内容区。
 * 对齐前端设计规范 §4 布局骨架:深墨蓝灰侧栏、浅灰内容底、绿色仅作点缀。
 */

import { useMemo, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  AlertOutlined,
  ApiOutlined,
  AuditOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  FileTextOutlined,
  LineChartOutlined,
  LogoutOutlined,
} from "@ant-design/icons";
import { Breadcrumb, Dropdown, Layout, Menu, Select } from "antd";

import { useAuthStore } from "@/stores/auth";
import logoSidebar from "@/assets/logo-sidebar.svg";
import logoIcon from "@/assets/logo-icon.svg";

const { Sider, Header, Content } = Layout;

interface NavItem {
  key: string;
  label: string;
  icon: React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { key: "/", label: "主页", icon: <DashboardOutlined /> },
  { key: "/servers", label: "服务器", icon: <CloudServerOutlined /> },
  { key: "/services", label: "服务", icon: <ApiOutlined /> },
  { key: "/deployments", label: "部署", icon: <DeploymentUnitOutlined /> },
  { key: "/approvals", label: "审批", icon: <AuditOutlined /> },
  { key: "/configs", label: "配置", icon: <FileTextOutlined /> },
  { key: "/monitoring", label: "监控", icon: <LineChartOutlined /> },
  { key: "/alerts", label: "告警", icon: <AlertOutlined /> },
];

const ENVS = [
  { value: "dev", label: "dev" },
  { value: "staging", label: "staging" },
  { value: "prod", label: "prod" },
];

export function AppLayout(): React.ReactElement {
  const [collapsed, setCollapsed] = useState(false);
  const [env, setEnv] = useState("dev");
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);

  const selectedKey = useMemo(() => {
    const match = NAV_ITEMS.find((item) => location.pathname.startsWith(item.key));
    return match?.key ?? "/servers";
  }, [location.pathname]);

  const currentLabel = NAV_ITEMS.find((item) => item.key === selectedKey)?.label ?? "";

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
        style={{ background: "#2F4050" }}
      >
        <div
          style={{
            height: 50,
            display: "flex",
            alignItems: "center",
            paddingLeft: collapsed ? 0 : 16,
            justifyContent: collapsed ? "center" : "flex-start",
            borderBottom: "1px solid #293846",
          }}
        >
          <img
            src={collapsed ? logoIcon : logoSidebar}
            alt="一脉 Axon"
            style={{ height: collapsed ? 24 : 26, color: "#A7B1C2" }}
          />
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          style={{ background: "transparent", borderRight: 0 }}
          items={NAV_ITEMS.map((item) => ({
            key: item.key,
            icon: item.icon,
            label: <Link to={item.key}>{item.label}</Link>,
          }))}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: "#FFFFFF",
            borderBottom: "1px solid #E7EAEC",
            padding: "0 20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            height: 50,
          }}
        >
          <Breadcrumb
            items={[{ title: "控制面" }, { title: currentLabel }]}
            style={{ fontSize: 13 }}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <Select
              size="small"
              value={env}
              onChange={setEnv}
              options={ENVS}
              style={{ width: 108 }}
            />
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
              <span style={{ cursor: "pointer", color: "#676A6C", fontSize: 13 }}>
                {user?.username ?? "未登录"}
              </span>
            </Dropdown>
          </div>
        </Header>
        <Content style={{ background: "#F3F3F4", padding: 20 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}

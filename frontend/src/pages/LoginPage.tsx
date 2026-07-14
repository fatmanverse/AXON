/**
 * 登录页。克制的居中卡片(非营销落地页),对齐设计规范:
 * 深墨蓝灰背景衬托白色登录卡,绿色仅用于主按钮。
 */

import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Alert, Button, Card, Form, Input } from "antd";
import { LockOutlined, UserOutlined } from "@ant-design/icons";

import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { colors } from "@/theme";
import logoSidebar from "@/assets/logo-sidebar.svg";

interface LoginForm {
  username: string;
  password: string;
}

interface LocationState {
  from?: { pathname: string };
}

export function LoginPage(): React.ReactElement {
  const [error, setError] = useState<string | null>(null);
  const login = useAuthStore((s) => s.login);
  const status = useAuthStore((s) => s.status);
  const navigate = useNavigate();
  const location = useLocation();

  const from = (location.state as LocationState | null)?.from?.pathname ?? "/servers";

  const handleSubmit = async (values: LoginForm): Promise<void> => {
    setError(null);
    try {
      await login(values.username, values.password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败,请稍后重试");
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: colors.sidebarBg,
      }}
    >
      <Card
        style={{ width: 360 }}
        styles={{ body: { padding: "28px 32px" } }}
        variant="borderless"
      >
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <img
            src={logoSidebar}
            alt="一脉 Axon"
            style={{ height: 30, filter: "invert(0.15)" }}
          />
          <div style={{ marginTop: 8, color: "#676A6C", fontSize: 13 }}>
            统一运维控制面
          </div>
        </div>
        {error && (
          <Alert
            type="error"
            message={error}
            showIcon
            style={{ marginBottom: 16 }}
            data-testid="login-error"
          />
        )}
        <Form<LoginForm> layout="vertical" onFinish={handleSubmit} requiredMark={false}>
          <Form.Item
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="用户名"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button
              type="primary"
              htmlType="submit"
              block
              loading={status === "loading"}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}

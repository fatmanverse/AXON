/**
 * 受保护路由:未认证跳登录页(记录来源以便登录后回跳),
 * 会话恢复中显示加载态,避免刷新瞬间误跳登录。
 */

import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Spin } from "antd";

import { useAuthStore } from "@/stores/auth";

export function ProtectedRoute(): React.ReactElement {
  const status = useAuthStore((s) => s.status);
  const restore = useAuthStore((s) => s.restore);
  const location = useLocation();

  useEffect(() => {
    if (status === "idle") {
      void restore();
    }
  }, [status, restore]);

  if (status === "idle" || status === "loading") {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}

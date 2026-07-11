/**
 * 路由守卫集成测试(T0.7)。
 * 验证:未认证访问受保护路由被重定向到登录页;已认证时渲染布局壳内容。
 * 用 MemoryRouter 控制初始路径,mock auth store 的会话态。
 */

import { describe, expect, it, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useAuthStore } from "@/stores/auth";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<div>登录页</div>} />
        <Route element={<ProtectedRoute />}>
          <Route path="/servers" element={<div>服务器页</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ user: null, status: "idle" });
});

describe("ProtectedRoute", () => {
  it("匿名用户被重定向到登录页", async () => {
    useAuthStore.setState({ status: "anonymous" });
    renderAt("/servers");
    expect(await screen.findByText("登录页")).toBeInTheDocument();
    expect(screen.queryByText("服务器页")).not.toBeInTheDocument();
  });

  it("已认证用户可进入受保护页", async () => {
    useAuthStore.setState({
      status: "authenticated",
      user: { username: "admin", roles: ["admin"], permissions: [] },
    });
    renderAt("/servers");
    expect(await screen.findByText("服务器页")).toBeInTheDocument();
  });

  it("会话恢复中显示加载态而非误跳登录", () => {
    useAuthStore.setState({ status: "loading" });
    renderAt("/servers");
    expect(screen.queryByText("登录页")).not.toBeInTheDocument();
    expect(screen.queryByText("服务器页")).not.toBeInTheDocument();
  });
});

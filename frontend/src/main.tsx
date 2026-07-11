/**
 * 应用入口:挂载 React Query、AntD 主题(定制 token)、路由。
 * 在此把 axios 的 401 处理接到路由:令牌失效时统一跳登录页。
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { setUnauthorizedHandler } from "./api/client";
import { antdTheme } from "./theme";
import "./styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

// 401 统一跳登录:令牌已在拦截器清理,这里只负责导航。
// 用 hash 之外的整页跳转避免与 React Router 实例耦合,登录后由 from 回跳。
setUnauthorizedHandler(() => {
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
});

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ConfigProvider theme={antdTheme} locale={zhCN}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);

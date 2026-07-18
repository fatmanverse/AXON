/**
 * 路由骨架:登录页(公开)+ 受保护布局壳下的各业务页。
 * 业务页 MVP 阶段用占位页打通联调,后续 Epic 逐页替换真实实现。
 */

import { Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { HomePage } from "@/pages/HomePage";
import { LoginPage } from "@/pages/LoginPage";
import { MonitoringPage } from "@/pages/MonitoringPage";
import { ServersPage } from "@/pages/ServersPage";
import { ServicesPage } from "@/pages/ServicesPage";
import { EnvironmentsPage } from "@/pages/EnvironmentsPage";
import { DeploymentsPage } from "@/pages/DeploymentsPage";
import { BuildsPage } from "@/pages/BuildsPage";
import { ConfigsPage } from "@/pages/ConfigsPage";
import { AlertsPage } from "@/pages/AlertsPage";
import { ApprovalsPage } from "@/pages/ApprovalsPage";

export function App(): React.ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route index element={<HomePage />} />
          <Route path="/servers" element={<ServersPage />} />
          <Route path="/services" element={<ServicesPage />} />
          <Route path="/environments" element={<EnvironmentsPage />} />
          <Route path="/deployments" element={<DeploymentsPage />} />
          <Route path="/builds" element={<BuildsPage />} />
          <Route path="/configs" element={<ConfigsPage />} />
          <Route path="/monitoring" element={<MonitoringPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/servers" replace />} />
    </Routes>
  );
}

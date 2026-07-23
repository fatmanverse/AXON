# Axon Initial Baseline

Date: `2026-07-20`
Status: `initial dual-baseline snapshot`

## 1. Purpose

记录当前产品要求与运行时边界，供后续非平凡改动判断需求是否就绪、owner 是否正确，以及实现是否发生漂移。

## 2. Workspace Structure

- `backend/`: FastAPI、业务编排、数据库模型、runtime 与外部集成适配器。
- `frontend/`: React/Vite 管理界面和 API 客户端。
- `agent/`: Go Agent 与 gRPC wire。
- `docs/`: 使用文档、设计文档与任务拆分。
- `ops/`: 升级和运行环境脚本。

## 3. Current Authority Surfaces

- 项目目标与能力边界：`README.md`。
- 产品和架构设计：`docs/统一运维控制面-设计文档.md`。
- 分期和任务边界：`docs/统一运维控制面-任务拆分.md`。
- 工作规则：仓库与全局 `AGENTS.md` 指令。
- 当前缺口：此前没有项目级 ADR、规格索引或双基线记录。

## 4. Product / Requirement Baseline

### 4.1 Current Truth

- 产品目标是把提交、扫描、构建、部署、监控和回滚串成可追溯交付链路。
- `service`、`git_sha`、`deployment_id` 是核心关联键；构建链新增 `build_id` 和 `artifact_id`。
- 写操作采用 task 异步模型，生产高危操作受审批和权限约束。
- 当前阶段已能构建并登记 artifact，但 artifact 尚未进入真实 runtime 部署编排。
- 验收以自动化测试、静态检查、构建和必要的运行边界验证为主要证据。

### 4.2 Non-negotiables

1. 不绕过生产审批、权限、质量门禁和审计。
2. 不用静默降级或假成功掩盖未支持的 runtime/策略。
3. 制品必须可追溯到 service、build 和 git SHA。
4. 旧 CI 部署路径保持兼容，除非另有明确迁移决定。

### 4.3 Product Non-goals

- 本阶段不实现任意跨服务制品部署。
- 本阶段不自动选择“最新制品”代替用户明确选择。
- 本阶段不承诺一次完成所有高级发布策略与多实例基础设施升级。

## 5. Architecture / Runtime Boundary Baseline

### 5.1 Current Truth

- `DeploymentService` 是部署 task/deployment 状态编排 owner。
- runtime 适配器负责把明确的运行时动作翻译为 Docker/systemd/Kubernetes 操作。
- `ArtifactRepository` 是制品记录读取与写入 owner。
- `release_strategy` 只负责已部署版本的发布策略铺开，不负责制品寻址或传输。
- `executor_factory` 负责按 server 接入方式构造执行器；机密由 `SecretStore` 提供。

### 5.2 Architecture Non-negotiables

1. API 层不承载制品兼容和 runtime 部署业务规则。
2. 制品传输使用窄接口注入，不扩大所有 `Executor` 的强制契约。
3. 制品直发与 CI 部署共享 task、审批、质量门禁和审计边界。
4. 任何部分部署必须明确落失败，不得返回成功。

### 5.3 Architecture Non-goals

- 不把 `rollout_provider` 扩张为制品传输与部署 owner。
- 不为首版引入消息总线、制品 CDN 或新的持久化聚合。

## 6. Ownership / Contract Snapshot

- 构建与 artifact 生成 → `BuildService` / `BuildRunner`。
- artifact 查询 → `ArtifactRepository`。
- 部署状态编排 → `DeploymentService`。
- runtime 命令翻译 → `DockerRuntime` / `SystemdRuntime` / `K8sRuntime`。
- 发布策略 → `release_strategy`。
- SSH/Agent 命令执行 → `Executor` 实现与 `executor_factory`。
- 前端构建和制品操作 → `BuildsPage` 与 `frontend/src/api/builds.ts`。

## 7. Current State and Risks

- generic artifact 是控制面本地 tar 路径，systemd 直发需要显式上传。
- Docker/K8s 可消费镜像 URI，但必须校验 artifact registry type。
- gRPC E2E 在受限沙箱中可能因禁止监听端口无法验证。
- 仓库仍存在历史 Black/Prettier 格式债务，不能与功能改动混为大范围重写。

## 8. Alignment Use

- 新功能先检查产品非协商项，再检查 owner、契约与依赖方向。
- 同时改变用户行为和 runtime 边界时报告 `scope: both`。
- 实现完成后用本基线判断是对齐、Design Defect 还是 Implementation Drift。

## 9. Compatibility Boundary

- 现有不带 `artifact_id` 的部署请求继续触发 CI。
- 现有部署、回滚、晋升、审批、任务轮询和 webhook 契约不得被无意改变。

## 10. 2026-07-23 Baseline Sync — Targeted Rollback and Deployment Exclusivity

Decision record: `docs/aegis/adr/ADR-0001-targeted-rollback-and-deployment-exclusivity.md`。

- rollback 的权威目标是同 service/env 的具体历史 deployment 快照；显式请求传 `target_deployment_id`，旧无 body 请求沿 current 的 `previous_deployment_id`。
- `DeploymentRepository` / `DeploymentService` 拥有目标校验和状态闭环；artifact 目标继续由 `ArtifactDeploymentService` 独占 runtime 规则，CI 目标继续使用 PipelineAdapter。
- `TaskRepository.create_deployment_operation()` 是 deploy/rollback task 的唯一创建入口；数据库部分唯一索引保证同一 service 同时最多一个 pending/running deploy/rollback。
- pending approval 不占部署槽；批准时若冲突返回 `409 deployment_in_progress`，approval 保持 pending。
- 部署历史 UI 以行级“回滚到此版本”替代顶部“一键回滚”，并展示 artifact id/URI；前端只选择 target ID，不拥有合法性规则。

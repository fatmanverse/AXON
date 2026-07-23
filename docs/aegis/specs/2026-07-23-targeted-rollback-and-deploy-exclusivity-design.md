# Targeted Rollback and Deployment Exclusivity Design

Date: `2026-07-23`
Status: `approved design; awaiting written-spec review`

## 1. TaskIntentDraft

- Outcome: 运维人员可从部署历史明确选择任意较早的成功版本回滚；artifact 直发记录沿 artifact runtime 原生回滚；同一服务不会并发执行 deploy/rollback。
- Goal: 修正现有“回滚重部署当前版”的语义漂移，补齐 artifact 可追溯回滚，并用数据库约束消除并发部署竞态。
- Success evidence: CI/artifact 双回滚路径、审批恢复、目标校验、并发 409、部署历史制品展示和行级回滚均有自动化测试；旧无 body rollback 调用保持兼容。
- Stop condition: 规格、实施计划、代码、生成契约、文档和验证全部完成；不扩张为通用发布队列。
- Non-goals: 跨 service/env 回滚、回滚失败自动重试、部署排队/取消、stale task 自动治理、任意 artifact ID 直接作为回滚请求。
- Scope risk: rollback、deploy、promotion 和审批共享 task owner；互斥必须由数据库保证，不能依赖前端 loading 或先查后写。

## 2. BaselineReadSetHint

- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md`
- `docs/统一运维控制面-设计文档.md`
- `docs/统一运维控制面-任务拆分.md`
- 当前 `DeploymentService`、`TaskRepository`、deployment/task models、rollback/approval API、`DeploymentsPage` 与相关测试。

## 3. BaselineUsageDraft

- Required baseline refs: 初始双基线、artifact 直发规格、项目设计与任务拆分。
- Delivered context refs: 用户确认“选择任意历史版本”和并发请求返回 409。
- Acknowledged before plan refs: deployment previous 链、artifact owner、task 状态机、审批恢复、部署历史 UI。
- Cited in design refs: 本规格第 2 节全部来源。
- Missing refs: 尚无已接受 ADR；完成时运行 ADR backfill 判断。
- Decision: `continue`。

## 4. Requirement Ready Check

- Requirement source refs: 当前对话中用户对目标回滚和并发拒绝的明确选择。
- Goals and scope refs: 本规格第 1 节。
- User / scenario refs: 运维人员从某 service 的部署历史选择一个较早的成功版本并回滚；并发操作发起者得到明确 409。
- Requirement item refs: 第 6 至第 12 节。
- Acceptance / verification criteria refs: 第 13 节。
- Open blocker questions: 无。
- Decision: `ready`。

## 5. ImpactStatementDraft

- Affected layers: rollback schema/API、approval payload、DeploymentService、deployment/task repositories、task persistence index、OpenAPI/TS schema、DeploymentsPage、运维文档。
- Canonical owners: `DeploymentService` 解析并执行目标 deployment；`ArtifactDeploymentService` 继续独占 artifact→runtime 规则；`TaskRepository` 创建互斥部署操作；数据库索引保证竞态安全。
- Invariants: rollback target 必须是同 service/env 的历史成功记录且不是当前成功记录；审批后仍执行请求时固定的 target；同 service 的 deploy/rollback 同时最多一个 active task。
- Compatibility: `POST /rollback` 无 body 时沿当前记录的 `previous_deployment_id` 回到上一版；CI deploy、promotion、webhook 和 artifact deploy 请求契约不变。
- Non-goals: API/UI 不复制 artifact/runtime 兼容规则；不新增第二套部署队列或锁表。

## 6. Confirmed Product Decisions

1. 用户从部署历史选择任意较早的成功 deployment 作为回滚目标。
2. 前端入口是历史行级“回滚到此版本”；现有顶部“一键回滚”入口退休。
3. 同一 service 已有 deploy/rollback task 处于 `pending` 或 `running` 时，新请求返回 `409 deployment_in_progress`。
4. 不排队，不把不同目标请求合并为同一 task。
5. artifact 目标走 `ArtifactDeploymentService`；无 artifact_id 的历史记录保持 CI 路径。
6. 部署历史展示 artifact_id/URI，完整值在详情中可追溯。

## 7. Options Considered

### Option A — API 先查 active task 再创建

- 优点: 无迁移，文本改动少。
- 缺点: 两个事务可同时查到“无 active task”并各自创建，不能满足并发不变量。

### Option B — TaskRepository + 数据库部分唯一索引（采用）

- 优点: 复用 task 作为运行中操作的 source of truth；请求竞态由数据库裁决；直接请求与审批批准共用一个创建入口。
- 缺点: 需要一条迁移；异常退出遗留的 active task 仍会阻塞后续操作，必须保留明确错误供运维核对。

### Option C — 新增 deployment operation 队列/锁表

- 优点: 可做 lease、排队、取消和恢复。
- 缺点: 引入新聚合、状态机和后台调度，超出本次“直接拒绝”决策。

## 8. API Contract

`POST /api/services/{service_id}/rollback` 接受可选 body：

```text
target_deployment_id: str | None
```

- 显式提供时：目标必须存在、属于同一 service/env、状态为 `success` 或 `rolled_back`，且不能是当前最新成功 deployment。
- 未提供时：读取当前最新成功 deployment 的 `previous_deployment_id`；缺失或目标非法返回 `409 no_rollback_target`。
- API 在创建 prod approval 前解析为一个具体 target ID，并把 `target_deployment_id` 写入 approval payload；批准等待期间历史变化不改变目标。
- artifact target 不要求 CI provider；CI target 缺 provider 返回 `503 pipeline_not_configured`。
- 并发冲突统一返回 `409 deployment_in_progress`，错误包含 active task ID（若可读取）供核对。

前端 API：

```text
rollbackService(serviceId, targetDeploymentId): Promise<DeployResult>
```

复用 `isPendingApproval` 和 `pollTaskUntilDone`。

## 9. Target Resolution and Rollback Flow

1. `DeploymentRepository` 读取当前最新成功 deployment 与目标 deployment。
2. 后端校验 target 的 service/env/status/current identity；前端禁用规则只用于体验，不拥有正确性。
3. 新 rollback deployment 的 `previous_deployment_id` 指向回滚前的当前 deployment，并复制目标的 version、git_sha、artifact、artifact_id 和 scan_result_id 快照。
4. target 带 `artifact_id`：先由 `ArtifactDeploymentService.resolve()` 验证 source of truth，再创建 running deployment，随后 `deploy()`；不得在 artifact 缺失时回退到字符串 URI。
5. target 不带 `artifact_id`：用目标 version/artifact 触发原 PipelineAdapter；缺 version 明确失败。
6. 执行成功后新 deployment/task 落 success，再把回滚前当前 deployment 落 `rolled_back`。
7. 执行失败时新 deployment/task 落 failed，当前 deployment 保持 success，不伪造闭环。

## 10. Deployment Exclusivity

- `tasks` 增加部分唯一索引 `uq_tasks_active_deployment_target`：对 `type IN (deploy, rollback)` 且 `status IN (pending, running)` 的记录，`target` 唯一。
- `TaskRepository.create_deployment_operation()` 是 deploy/rollback/promotion/批准执行创建 task 的单一入口；捕获唯一冲突并翻译为 `AppError("deployment_in_progress", ..., 409)`。
- 普通 lifecycle、build、config task 继续走通用 `create()`，不被该索引互斥。
- pending approval 本身不占锁；批准时若服务正忙，批准请求返回 409，approval 保持 pending，可稍后重试。
- `unknown` 不纳入 active 索引；当前 deploy/rollback 编排只落 success/failed，若未来引入 unknown，必须另行定义恢复策略。

## 11. Frontend Behavior

- 删除 DeploymentsPage 顶部“一键回滚”按钮，避免与行级目标选择形成重复入口。
- 表格计算最新 `success` 记录为当前版本；对更早的 `success`/`rolled_back` 行显示“回滚到此版本”，当前、running、failed 行不提供回滚操作。
- confirmation 展示目标 version、artifact_id/URI、时间和当前 service/env。
- pending approval 只提示审批，不轮询；直接受理后按 task success/failed/unknown 回显并刷新 deployments。
- 增加“制品”列：artifact_id 显示短 ID，CI 记录显示 artifact URI；部署详情展示完整 artifact_id 和 URI。无值显示统一空态。

## 12. Error Contract

- `rollback_target_not_found` — 404。
- `rollback_target_mismatch` — 409（跨 service/env）。
- `rollback_target_invalid` — 409（failed/running/缺 CI version）。
- `rollback_target_is_current` — 409。
- `no_rollback_target` — 409（兼容无 body 请求没有 previous）。
- `deployment_in_progress` — 409。
- artifact/CI/runtime 原有错误保持原码，不降级、不吞错。

## 13. Automated Acceptance

1. Repository/schema: 显式/隐式 target 解析、跨 service/env、非法状态、当前目标均有测试。
2. Artifact rollback: 不调用 CI；调用 artifact deployer；新记录复制 artifact_id/URI/version/git_sha；成功闭环，失败保持当前成功状态。
3. CI rollback: 仍调用 PipelineAdapter 并保存 pipeline_id；旧无 body API 回到 previous deployment。
4. Approval: payload 固定 target ID；artifact target 无 provider 可批准；忙时批准返回 409 且 approval 仍 pending。
5. Exclusivity: 直接 deploy、rollback、promotion 和批准创建共用 repository 方法；并发冲突由唯一索引证明为 409。
6. Frontend: artifact 显示、行级目标 payload、confirmation、pending approval、task success/failed 和刷新均有集成测试。
7. Contract/docs: `make gen-api` 只出现 rollback body 与批准字段相关变化；使用文档说明目标回滚和 409。
8. Regression: backend targeted/full/static、frontend lint/test/build、迁移 upgrade、`git diff --check`。

## 14. Product Risk Lens

- Value: 回滚真正回到用户指定的已知版本，artifact 直发链路具备可操作的恢复闭环。
- Non-goals: 不提供排队、取消、跨环境回滚或自动选择任意 artifact。
- Trade-offs: 直接 409 简单且可审计，但异常遗留 active task 需要运维先核对任务状态。
- Decision needed: 用户确认书面规格后进入实施计划。

## 15. Architecture Integrity Lens

- Invariant: deployment snapshot 决定回滚目标；artifact runtime 规则仍只有 `ArtifactDeploymentService` 一个 owner；task/database 共同拥有并发不变量。
- Canonical owner / contract: target validation 与状态闭环在 `DeploymentService`/repository；API 只解析 transport/approval；前端只选择 ID。
- Responsibility overlap: 不在 DeploymentsPage 推断 artifact 兼容，不在 API 重写 runtime 分支，不新增内存锁。
- Higher-level simplification: 用一个 exclusive task creator 替换 deploy/rollback/approval 各自无约束创建；删除顶部重复回滚入口。
- Retirement / falsifier: 若直接请求和批准仍有任一处绕过 exclusive creator，或 artifact rollback 直接调用 runtime adapter，设计失败。
- Verdict: `aligned`。

## 16. Baseline Role Alignment

- Product / Requirement Baseline: 可追溯交付链、真实回滚、失败不假成功、生产审批。
- Architecture / Runtime Boundary Baseline: DeploymentService 状态 owner、ArtifactDeploymentService runtime owner、task 异步模型。
- Result: 原“重部署当前版”测试与设计文档冲突，属于 `Implementation Drift`；本规格返回已确认 baseline。
- scope: `both`。
- Next action: 用户确认书面规格后进入 writing-plans。

## 17. Complexity Budget

- Artifact class: persistence invariant / cross-module workflow / frontend interaction。
- Target files: task model/migration/repository、deployment repository/service、services/approvals API、rollback schema、DeploymentsPage/API/tests、生成契约和文档。
- Current pressure: `services.py` 与 `DeploymentService` 已较大；不得把 target 校验或唯一冲突处理复制到多个 API handler。
- Projected pressure: 复用 repository helper 为 `at-risk`；API 内多处先查后写为 `over-budget`。
- Planned governance: repository 提供 target query 和 exclusive create；DeploymentService 分出窄 rollback helpers；前端只新增列与行 action。

Plan-Time Complexity Check:

- Better file boundary: migration + repository owner；DeploymentService 保持编排，必要时提取窄私有 helper，不新增第二 service。
- Recommendation: `edit-in-place` repository/service，拒绝 API/approval 重复分支。

## 18. ADR Signal

- Trigger: yes。
- Durable surface: rollback API 目标契约、active deploy 数据库不变量、task creation owner。
- Alternatives: query-only、partial unique index、新操作队列。
- Expected follow-up: 实施验证后判断创建或补充 ADR，并同步初始 baseline 中的 rollback/互斥边界。

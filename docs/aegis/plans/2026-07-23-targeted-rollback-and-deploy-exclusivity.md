# Targeted Rollback and Deployment Exclusivity Implementation Plan

Date: `2026-07-23`
Status: `approved for inline execution`

## Goal

让运维人员从部署历史选择任意较早的成功 deployment 回滚；artifact 历史版本沿原生 runtime 路径恢复；同一 service 的 deploy/rollback 在数据库层严格互斥，并保持旧无 body rollback、CI 部署、审批、promotion 与 webhook 兼容。

## Architecture

- `DeploymentRepository` 负责读取并验证 rollback 当前版本与目标快照。
- `DeploymentService` 负责 CI/artifact 双路径编排和 deployment/task 状态闭环。
- `ArtifactDeploymentService` 继续独占 artifact 到 runtime 的解析与执行规则。
- `TaskRepository.create_deployment_operation()` 是 deploy/rollback 操作 task 的唯一创建入口；数据库部分唯一索引裁决竞态。
- services/approvals API 只处理 transport、鉴权、门禁、审批 payload 与依赖装配。
- `DeploymentsPage` 只选择历史 deployment ID、展示制品并复用既有审批与 task polling。

## Tech Stack

- Backend: Python、FastAPI、SQLAlchemy async、Alembic、Pydantic、pytest。
- Frontend: React、TypeScript、Ant Design、TanStack Query、Vitest。
- Contract: OpenAPI export、openapi-typescript。

## Baseline / Authority Refs

- `docs/aegis/specs/2026-07-23-targeted-rollback-and-deploy-exclusivity-design.md`
- `docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md`
- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `docs/统一运维控制面-设计文档.md`
- `docs/统一运维控制面-任务拆分.md`

## Compatibility Boundary

- `POST /api/services/{service_id}/rollback` 无 body 时，沿当前成功记录的 `previous_deployment_id` 回到上一版。
- 显式 `target_deployment_id` 只接受同 service/env、状态为 `success` 或 `rolled_back`、且不是当前版本的历史记录。
- artifact 目标不要求 pipeline provider；CI 目标继续使用原 PipelineAdapter。
- pending approval 不占部署锁；批准创建 task 冲突时 approval 仍为 pending。
- promotion 继续创建 `deploy` task，因此与目标 service 的 deploy/rollback 互斥。
- lifecycle/build/config/webhook 不受该互斥索引影响。

## Verification

- Backend targeted: migration/model、task repository、rollback service/API、approval flow、deploy/promotion regression。
- Frontend targeted: `DeploymentsPage.test.tsx`。
- Contract/docs: `make gen-api`、生成 diff、使用文档。
- Full: backend tests（沙箱允许范围）、Ruff、Black、frontend test/lint/build、Alembic upgrade、`git diff --check`。

## Plan Basis

### Facts

- 当前 rollback 重部署最近成功 deployment 自身，未沿 `previous_deployment_id`，与设计语义冲突。
- 当前 rollback 在读取目标前无条件构造 PipelineAdapter，artifact 历史无法原生回滚。
- deploy、rollback、promotion 和 approval approve 分散调用通用 `TaskRepository.create()`，并发下没有互斥。
- `tasks.target` 已统一使用 `service:<id>`，可作为互斥键。
- `Deployment` 已持久化 `artifact_id`、URI、version、git SHA 与 previous 快照。

### Assumptions

- 数据库支持 PostgreSQL 部分唯一索引；SQLite 测试使用等价 `sqlite_where`。
- 既有 active deploy/rollback 重复数据不是正常状态；迁移若发现重复应显式失败，不能静默删任务。
- `TaskStatus.UNKNOWN` 不计入本次 active 定义。

### Known Environment Limits

- gRPC E2E 在受限沙箱中可能因禁止监听端口失败。
- Go Agent 需要 Go 1.25；当前环境若仍为 1.24 且无法下载，只报告该外部验证缺口。

## BaselineUsageDraft

- Required baseline refs: 本计划 Baseline / Authority Refs 全部条目。
- Acknowledged before plan refs: rollback previous 链、artifact runtime owner、task 状态机、approval payload、部署历史 UI。
- Cited in plan refs: 本计划 Baseline / Authority Refs。
- Missing refs: 无阻塞项；完成时进行 ADR backfill 判断。
- Decision: `continue`。

## Requirement Ready Check

- Requirement source refs: 已批准 Design Spec 与用户明确选择。
- Goals and scope refs: Design Spec 第 1、6、8-13 节。
- User / scenario refs: 从部署历史选择任意较早成功版本；同 service 并发操作直接得到 409。
- Acceptance / verification criteria refs: Design Spec 第 13 节与本计划各任务 Verification。
- Open blocker questions: 无。
- Decision: `ready`。

## Change Necessity

- User-visible need: 当前回滚目标错误，artifact 历史无法恢复，并发部署可同时受理。
- No-change / non-code option: 文档或前端禁用无法修复后台语义与请求竞态。
- Why code change is necessary: 必须修改 API 契约、持久化约束、repository owner、编排分支和 UI。
- Minimum change boundary: 复用现有 deployment/task/artifact owner，不新增队列、锁表或第二套 runtime 规则。
- Decision: `code-change`。

## Existence Check

- Proposed new surface: Alembic 部分唯一索引、`TaskRepository.create_deployment_operation()`、rollback body。
- Existing owner / reuse candidate: `tasks` 表、TaskRepository、DeploymentService、既有 rollback endpoint。
- Why existing surface is insufficient: 通用 create 无法翻译竞态；旧 endpoint 无目标 ID；仅先查无法保证原子性。
- Creation proof: 数据库索引是并发不变量的最终裁决；repository helper 是所有调用方的单一翻译入口。
- Entropy / retirement impact: 所有 deploy/rollback/promotion/approval 创建点迁移后，禁止继续直接调用通用 create。
- Decision: `add-with-proof`。

## Architecture Integrity Lens

- Invariant: 每个 service 最多一个 pending/running deploy/rollback task；rollback 目标由 deployment snapshot 决定。
- Canonical owner / contract: 数据库+TaskRepository 拥有互斥，DeploymentRepository/Service 拥有目标与闭环，ArtifactDeploymentService 拥有 runtime 规则。
- Responsibility overlap: API/前端不得复制目标合法性或 artifact 兼容规则。
- Higher-level simplification: 一个 exclusive creator 取代四处无约束创建；行级入口取代顶部重复回滚入口。
- Retirement / falsifier: 任一 deploy/rollback 创建点绕过 exclusive creator，或 artifact rollback 绕过 artifact deployer，均视为失败。
- Verdict: `aligned`。

## Plan Pressure Test

- Owner / contract / retirement: owner 和删除路径明确。
- Architecture integrity / higher-level path: 不引入内存锁、队列、caller-side fallback。
- Verification scope: repository 竞态、API、approval、CI/artifact 编排、UI 与生成契约均覆盖。
- Task executability: 每项有精确文件、RED/GREEN 命令和停止边界。
- Pressure result: `proceed`。

## Plan-Time Complexity Check

Complexity Budget:

- Artifact class: persistence invariant / cross-module workflow / frontend interaction。
- Target files: `services.py`、`approvals.py`、`deployment_service.py`、repositories、DeploymentsPage。
- Current pressure: API 与 DeploymentService 已较大，复制校验会形成多个 owner。
- Projected post-change pressure: repository helper + 窄私有编排 helper 为 `at-risk`；API 内实现业务规则为 `over-budget`。
- Budget result: `at-risk`。
- Planned governance: migration/repository 建立不变量；DeploymentService 分 CI/artifact 私有路径；API 仅传 ID。

Plan-Time Complexity Check:

- Target files: `backend/app/api/services.py`、`backend/app/api/approvals.py`、`backend/app/services/deployment_service.py`、`frontend/src/pages/DeploymentsPage.tsx`。
- Existing size / shape signals: 多职责 router 与编排类已接近结构压力边界。
- Owner fit: 薄接线可原位修改，校验与冲突翻译必须下沉到 repository/service。
- Add-in-place risk: API 复制 target 校验、approval 复制 task 创建、前端推断后端合法性。
- Better file boundary: 复用两个 repository；不新增 service。
- Recommendation: repository/service `edit-in-place`，API/UI 仅薄改。

## File Map

### Create

- `backend/alembic/versions/20260723_1200_d4e5f6a7b8c9_active_deployment_task_exclusivity.py`
- `backend/tests/integration/test_deployment_task_exclusivity.py`

### Modify

- `backend/app/models/task.py`
- `backend/app/services/task_repository.py`
- `backend/app/services/deployment_repository.py`
- `backend/app/services/deployment_service.py`
- `backend/app/schemas/service.py`
- `backend/app/api/services.py`
- `backend/app/api/approvals.py`
- `backend/tests/integration/test_task_repository.py`
- `backend/tests/integration/test_rollback_service.py`
- `backend/tests/integration/test_rollback_api.py`
- `backend/tests/integration/test_approval_flow_api.py`
- `backend/tests/integration/test_deployment_service.py`
- `frontend/src/api/deployments.ts`
- `frontend/src/pages/DeploymentsPage.tsx`
- `frontend/tests/integration/DeploymentsPage.test.tsx`
- `backend/openapi.json`
- `frontend/src/api/schema.d.ts`
- `docs/使用与部署.md`

## Task 1: Establish the Active Deployment Task Invariant

**Files:** task model、new migration、TaskRepository、task repository/exclusivity tests。

**Why:** 先查后写不能阻止两个事务同时创建；数据库必须成为竞态裁决者。

**Change Necessity:** 非代码或前端禁用无法建立原子互斥；最小边界是一个部分唯一索引和一个 repository 创建入口。

**Impact / Compatibility:** 只约束 `type IN ('DEPLOY','ROLLBACK')` 且 `status IN ('PENDING','RUNNING')` 的同 target；终态和其他 task 类型不受影响。

**Target contract:** `TaskRepository.create_deployment_operation(type, service_id, payload, created_by)` 只接受 DEPLOY/ROLLBACK，写入 `target=f"service:{service_id}"`，在 savepoint 中 flush；唯一冲突查询 active task 并抛 `AppError("deployment_in_progress", ..., 409)`，其他 IntegrityError 原样抛出。

- [ ] **Write test:** 覆盖同 target active deploy→deploy/rollback 冲突、不同 target 成功、终态后可创建、其他类型不受限、数据库直接并发写只有一个成功。
- [ ] **Verify RED:** `cd backend && UV_CACHE_DIR=/tmp/axon-uv-cache uv run pytest tests/integration/test_task_repository.py tests/integration/test_deployment_task_exclusivity.py -q`；预期缺索引/helper 失败。
- [ ] **Minimal code:** 添加 SQLAlchemy `Index`（postgresql_where/sqlite_where）、Alembic upgrade/downgrade、repository helper 与 active query；迁移不清理重复 active 数据。
- [ ] **Verify GREEN:** 重跑 RED 命令；再执行 `UV_CACHE_DIR=/tmp/axon-uv-cache uv run alembic upgrade head`（临时测试库环境允许时）与 Ruff/Black targeted。
- [ ] **Commit:** 暂不提交；工作区包含用户既有改动，最终统一交付 diff。

**Repair Track:** 根因是多个 caller 使用无约束通用 create；canonical owner 为 DB index + TaskRepository。

**Retirement Track:** Task 2 必须迁移所有 deploy/rollback/promotion/approval caller；完成后 `rg` 不得发现这些路径调用通用 create。

## Task 2: Route Every Deployment Operation Through the Exclusive Creator

**Files:** services API、approvals API、相关 deploy/promotion/approval tests。

**Why:** 部分唯一索引只有在统一错误翻译和审批状态处理下才能形成稳定 API 契约。

**Change Necessity:** 现有四处 caller 会泄露 IntegrityError 或绕过规范；最小边界是替换 task 创建调用，不重写 router。

**Impact / Compatibility:** 成功响应不变；冲突新增 `409 deployment_in_progress`；批准冲突时不更新 approval。

- [ ] **Write test:** direct deploy、rollback、promotion 冲突返回 409；approval approve 冲突返回 409 且 approval 仍 pending/task_id 为空。
- [ ] **Verify RED:** `cd backend && UV_CACHE_DIR=/tmp/axon-uv-cache uv run pytest tests/integration/test_deploy_api.py tests/integration/test_rollback_api.py tests/integration/test_approval_flow_api.py -q`；预期冲突路径失败。
- [ ] **Minimal code:** 所有 deploy/rollback task 使用 `create_deployment_operation()`；approval 先成功创建 task 再更新 approved 状态，保持同一事务。
- [ ] **Verify GREEN:** 重跑 RED 命令，并以 `rtk rg -n 'TaskRepository.*create|\.create\(' backend/app/api/services.py backend/app/api/approvals.py` 人工确认仅非部署 task 使用通用 create。
- [ ] **Commit:** 暂不提交。

**Repair Track:** 统一所有 producer，错误由 repository 翻译。

**Retirement Track:** 删除 caller-side active 查询或 IntegrityError 处理；不得保留第二套锁逻辑。

## Task 3: Add Explicit Rollback Target Resolution

**Files:** service schema、DeploymentRepository、rollback API、repository/API tests。

**Why:** 用户必须能固定选择历史 deployment，旧调用必须真正沿 previous 链回到上一版。

**Change Necessity:** 当前 API 没有 body，service 只查 current；最小边界是可选 body、repository resolver 和 approval payload 固化。

**Impact / Compatibility:** body 可省略；显式目标错误码按批准设计稳定返回。

**Target contract:** `RollbackRequestBody(target_deployment_id: str | None = None)`；repository resolver 返回 `(current, target)`，显式目标校验存在、same service/env、success/rolled_back、not current；隐式目标取 current.previous_deployment_id 并应用同样校验。

- [ ] **Write test:** 显式目标成功；not found/mismatch/invalid/current；无 body 沿 previous；无 previous 返回 no_rollback_target；prod approval payload 保存具体 target ID。
- [ ] **Verify RED:** `cd backend && UV_CACHE_DIR=/tmp/axon-uv-cache uv run pytest tests/integration/test_deployment_repository.py tests/integration/test_rollback_api.py tests/integration/test_approval_flow_api.py -q`。
- [ ] **Minimal code:** 实现 body/resolver；API 在审批或 task 创建前解析具体 target ID；background/approval 调用均传该 ID。
- [ ] **Verify GREEN:** 重跑 RED 命令与 targeted Ruff/Black。
- [ ] **Commit:** 暂不提交。

**Repair Track:** 修复“重部署 current”与 previous 链契约冲突。

**Retirement Track:** 删除 `_execute_rollback()` 内自行选择 current 作为目标的旧逻辑与对应旧断言。

## Task 4: Execute CI and Artifact Rollbacks Through Their Canonical Owners

**Files:** DeploymentService、rollback/deployment service tests。

**Why:** 指定 artifact 历史版本必须真实恢复到 runtime，不能伪装成 CI artifact URI 参数。

**Change Necessity:** 当前 rollback 无 artifact 分支且总需 adapter；最小边界是复制目标快照后按 `artifact_id` 分派现有 owner。

**Impact / Compatibility:** CI 目标保留 pipeline_id；artifact 目标不调用 CI；失败时 current 保持 success。

**Target contract:** `run_rollback(..., target_deployment_id: str)`；新 rollback deployment 的 previous 指向回滚前 current，并复制 target version/git_sha/artifact/artifact_id/scan_result_id。artifact 路径先 `resolve()` 再落 running 并 `deploy()`；CI 路径要求 version/provider。成功后新记录 success、current rolled_back；失败只落新记录/task failed。

- [ ] **Write test:** CI 指定旧版本及 pipeline_id；artifact 不调用 CI且调用 deployer；快照字段完整；resolve/deploy/CI 失败闭环；current 不被错误标记；无 provider 的 artifact 成功、CI 503。
- [ ] **Verify RED:** `cd backend && UV_CACHE_DIR=/tmp/axon-uv-cache uv run pytest tests/integration/test_rollback_service.py tests/integration/test_deployment_service.py -q`。
- [ ] **Minimal code:** 提取窄 `_execute_ci_rollback` / `_execute_artifact_rollback` 或等价私有 helper，共享状态闭环，不复制 artifact/runtime 规则。
- [ ] **Verify GREEN:** 重跑 RED 命令以及 auto-rollback/deploy regression；执行 targeted Ruff/Black。
- [ ] **Commit:** 暂不提交。

**Repair Track:** target snapshot 是唯一回滚输入，DeploymentService 只编排。

**Retirement Track:** 删除 artifact URI 回退、current-as-target 旧注释和测试。

## Task 5: Replace One-click Rollback with Deployment-history Actions

**Files:** frontend deployment API/page/integration test。

**Why:** 用户需要看到制品并明确选择历史版本，避免含糊的顶部入口。

**Change Necessity:** 当前页面只能一键回滚且不展示 artifact；最小边界是表格列、行 action 和确认 Modal。

**Impact / Compatibility:** 部署按钮保留；rollback API 返回类型扩展为 `DeployResult` 以处理 prod approval。

- [ ] **Write test:** 展示短 artifact_id/URI 与详情完整值；仅较早 success/rolled_back 显示 action；确认文案；POST target ID；pending approval 不轮询；task success/failed/unknown 提示并刷新；顶部按钮不存在。
- [ ] **Verify RED:** `cd frontend && npm run test -- DeploymentsPage.test.tsx`；预期旧一键回滚行为导致失败。
- [ ] **Minimal code:** `rollbackService(serviceId, targetDeploymentId)`；计算当前 success；AntD Modal.confirm 或受控 Modal；复用 `isPendingApproval`、`pollTaskUntilDone` 和 query invalidation。
- [ ] **Verify GREEN:** 重跑 targeted test，再执行 `npm run lint` 与 `npm run build`。
- [ ] **Commit:** 暂不提交。

**Repair Track:** 前端只发送 deployment ID，不实现后端合法性。

**Retirement Track:** 删除顶部“一键回滚”、Popconfirm import 与旧测试。

## Task 6: Regenerate Contracts, Document Behavior, and Verify the Whole System

**Files:** generated OpenAPI/TS schema、使用文档、全部已改文件。

**Why:** API body、错误和操作方式必须与客户端和运维文档一致。

**Change Necessity:** 生成契约与文档是本次 public API 变更的一部分；不能手写 schema.d.ts。

**Impact / Compatibility:** 生成 diff 应只反映 rollback body/相关 schema；非预期契约变化必须回查源 schema。

- [ ] **Write test:** 先以现有 backend/frontend 自动化验收作为契约测试；文档明确 targeted rollback、artifact 展示、approval 与 409。
- [ ] **Verify RED:** `rtk make gen-api` 前确认 generated files 尚无 `target_deployment_id`。
- [ ] **Minimal code:** 运行 `UV_CACHE_DIR=/tmp/axon-uv-cache rtk make gen-api`；更新 `docs/使用与部署.md`；审查生成 diff。
- [ ] **Verify GREEN:** backend targeted/full/static、frontend test/lint/build、迁移 upgrade、`rtk git diff --check`；记录沙箱外部限制。
- [ ] **Commit:** 暂不提交；向用户交付完整 diff 与验证证据。

**Repair Track:** 契约从 Pydantic source 生成，文档不成为第二实现 owner。

**Retirement Track:** 无旧生成路径保留；若 generated diff 出现无关漂移，回滚生成环境差异而非接受噪声。

## Risks and Rollback Surface

- 部分唯一索引可能暴露已有重复 active task；迁移应失败并要求人工核对，不自动删除审计记录。
- approval approve 必须在 task 创建成功后才落 approved，避免“批准成功但无 task”。
- artifact resolve 与 deploy 之间 artifact 元数据理论上可变化；当前 artifact 记录视为不可变快照，deployment 仍保存执行快照。
- rollback 失败不得把 current 标记 rolled_back。
- 代码回退时先回退 caller 到通用 create，再 downgrade 索引会重新开放竞态；仅作为完整版本回退，不作为运行时开关。

## Self-review

- Spec coverage: target resolution、CI/artifact、approval 固化、DB 互斥、UI、contracts/docs/full verification 均有任务。
- Placeholder scan: 无 TODO/TBD/模糊“后续实现”。
- Type consistency: rollback body 与 frontend payload 均为 `target_deployment_id`；background/approval/service 传具体 ID。
- Compatibility: 无 body、CI、promotion、webhook、非部署 task 均明确。
- Change necessity/existence: 新索引/helper/body 均有最小边界与创建证明。
- Architecture integrity: 无 caller-side fallback、无第二 runtime owner、无内存锁。
- Verification: 每个 slice 有 RED/GREEN 命令，最终有全量门禁。
- Dual track: 每个结构性任务均含 Repair/Retirement Track。


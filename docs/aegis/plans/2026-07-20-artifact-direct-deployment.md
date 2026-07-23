# Artifact Direct Deployment Implementation Plan

Date: `2026-07-20`
Status: `ready for execution choice`

## Goal

把控制面已登记的 artifact 真实部署到其所属 service 的 systemd、Docker 或 Kubernetes runtime，完成“代码 → 构建 → artifact → deployment → runtime”闭环，同时保持现有 CI 部署、审批、质量门禁、回滚和 promotion 行为不变。

## Architecture

- `DeploymentService` 继续拥有 task/deployment 状态编排。
- 新 `ArtifactDeploymentService` 拥有 artifact 校验、runtime 兼容、placement 解析和直接部署执行。
- 新窄接口 `ArtifactTransfer` 只负责本地 generic artifact 的 SSH/SFTP 上传，不扩大全部 `Executor` 的抽象契约。
- `DockerRuntime`、`SystemdRuntime`、`K8sRuntime` 继续拥有运行时命令/API 翻译。
- API 只负责 transport、权限、门禁、审批和依赖装配；前端只提供 artifact 行级入口。

## Tech Stack

- Backend: Python 3.11+、FastAPI、SQLAlchemy async、Pydantic v2、asyncssh、kubernetes-asyncio、pytest。
- Frontend: React 18、TypeScript、Ant Design、TanStack Query、Vitest。
- Contract generation: OpenAPI export + `openapi-typescript`。

## Baseline / Authority Refs

- `docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md`
- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `docs/aegis/BASELINE-GOVERNANCE.md`
- `README.md`
- `docs/统一运维控制面-设计文档.md`
- `docs/统一运维控制面-任务拆分.md`

## Compatibility Boundary

- 不带 `artifact_id` 的 `POST /api/services/{id}/deploy` 继续要求 `version` 并触发现有 CI。
- artifact 模式不调用 CI，也不调用 `execute_release_strategy`，避免直发后重复 restart/scale。
- 现有 approval、quality gate、task polling、rollback、promotion、webhook 接口保持兼容。
- artifact 只能部署到所属 `service_id`；跨环境继续使用 promotion。
- artifact 直发首版仅支持 `rolling`。

## Verification

- Backend target tests: repository/schema、SFTP、artifact deploy service、deployment service、API/approval。
- Backend regression: `cd backend && uv run pytest tests/ --ignore=tests/integration/test_agent_grpc_e2e.py`（受限沙箱）；正常主机运行完整 `tests/`。
- Backend static: `cd backend && uv run ruff check app tests`，对改动文件执行 Black check。
- Frontend: `cd frontend && npm run lint && npm run test && npm run build`。
- Contract: `make gen-api` 后确认 OpenAPI 与 TypeScript schema diff 只包含批准字段。

## Plan Basis

### Facts

- `Artifact` 已保存 `service_id`、registry、URI、version 和 git SHA。
- `Deployment` 已有 `artifact_id` 列，但 repository 和输出 schema 尚未使用。
- 三个 runtime 已有 `deploy(DeploySpec)` 原语。
- generic artifact URI 是控制面本地 tar 路径；systemd 目标机不能直接读取。
- 当前部署 API 在检查 service 前就要求 CI provider，artifact 模式必须调整该顺序。

### Assumptions

- Docker artifact URI 是目标环境可拉取的镜像坐标。
- systemd 的 `runtime_ref` 提供 `unit_name` 和 `deploy_path`。
- Docker 的 `runtime_ref` 提供 `container_name`，可选 `env` 和 `ports`。
- K8s 的 `runtime_ref` 提供 `namespace` 和 `workload`。

### Known Environment Limits

- 当前沙箱禁止真实 gRPC 监听，因此对应 2 个 E2E 只能在正常主机补跑。
- 当前沙箱 `.git` 只读，实施时提交可能需要用户终端或临时克隆交付。

## BaselineUsageDraft

- Required baseline refs: 批准的 Design Spec、初始双基线、README、设计和任务拆分文档。
- Delivered context refs: 用户确认的显式 artifact、同服务、类型前置校验、构建页入口、SFTP 上传选择。
- Acknowledged before plan refs: artifact/build/deployment 模型、repository、API/approval、runtime、executor factory、BuildsPage。
- Cited in plan refs: 本计划 Baseline / Authority Refs。
- Missing refs: 无已接受 ADR；完成时运行 ADR Auto Backfill 判断。
- Decision: `continue`。

## Requirement Ready Check

- Requirement source refs: 批准的 Design Spec 和当前对话选择。
- Goals and scope refs: Design Spec 第 1、6、9-14 节。
- User / scenario refs: 运维人员从构建页选择所属 service 的具体 artifact 并直发。
- Requirement item refs: 同服务限制、类型/runtime 映射、SFTP、rolling-only、CI 兼容。
- Acceptance / verification criteria refs: Design Spec 第 14 节和本计划各任务 Verification。
- Open blocker questions: 无。
- Decision: `ready`。

## Change Necessity

- User-visible need: 构建产物当前无法从 UI 真实部署到 runtime。
- No-change / non-code option: 继续人工复制或依赖外部 CI，不能闭合控制面自建交付链。
- Why code change is necessary: 请求契约、artifact 读取、上传、runtime 调用、状态和 UI 均缺少生产接线。
- Minimum change boundary: 复用现有表、端点、task/approval/runtime；仅新增 artifact deploy owner 和窄传输接口。
- Decision: `code-change`。

## Existence Check

- Proposed new surface: `ArtifactDeploymentService`、`ArtifactTransfer`、SSH/SFTP 实现。
- Existing owner / reuse candidate: `DeploymentService`、`rollout_provider`、`Executor`。
- Why existing surface is insufficient: 状态编排不应拥有连接细节；release strategy 不拥有制品；通用 Executor 不应强制文件上传。
- Creation proof: generic artifact 必须从控制面传至 systemd 目标，三 runtime 需要一个统一兼容与 target 解析 owner。
- Entropy / retirement impact: 新 owner 替代 API/runtime 多处重复类型映射；禁止保留第二套直发规则。
- Decision: `add-with-proof`。

## Architecture Integrity Lens

- Invariant: artifact 身份、类型和 URI 只有一个权威来源；所有可静态验证在远端动作前完成。
- Canonical owner / contract: `ArtifactDeploymentService` 拥有 artifact→runtime 执行，`DeploymentService` 拥有状态，runtime adapter 拥有动作翻译。
- Responsibility overlap: API 不实现 runtime 分支；`release_strategy` 不接收 artifact。
- Higher-level simplification: 复用 existing task、approval、quality gate、runtime adapter、executor factory 和 SecretStore。
- Retirement / falsifier: 若实施后 API 或 rollout provider 仍出现第二套类型映射，必须删除该重复逻辑。
- Verdict: `aligned`。

## Plan Pressure Test

- Owner / contract / retirement: owner 明确，无旧 artifact 直发路径需保留。
- Architecture integrity / higher-level path: 通过独立 service 避免扩大 952 行 API 和 425 行 DeploymentService。
- Verification scope: producer、consumer、approval、runtime、UI 和旧 CI 回归均覆盖。
- Task executability: 每个任务有明确文件、测试和提交边界。
- Pressure result: `proceed`。

## Plan-Time Complexity Check

Complexity Budget:

- Artifact class: Source Complexity、Test Complexity、Decision / Plan Complexity。
- Target files / artifacts: `services.py` 952 行、`DeploymentService` 425 行、`BuildsPage` 392 行，以及新 service/transfer 文件。
- Current pressure: 现有 API 已超过 800 行，DeploymentService/BuildsPage 已有多个职责块。
- Projected post-change pressure: 薄接线 + 新 owner 为 `at-risk` 但可治理；全部写入现有文件将 `over-budget`。
- Budget result: `at-risk`。
- Planned governance: API 仅增加参数和调用；runtime 逻辑放新 service；前端提取 artifact action handler，不新增第二页面。

Plan-Time Complexity Check:

- Target files: `backend/app/api/services.py`、`backend/app/services/deployment_service.py`、`frontend/src/pages/BuildsPage.tsx`。
- Existing size / shape signals: API 952 行已触发 major pressure；其余两个文件接近多职责边界。
- Owner fit: transport/status/UI owner 合理，但 runtime/transfer 不适合写入这些文件。
- Add-in-place risk: 新增大段 runtime 分支会形成重复 owner。
- Better file boundary: `artifact_deployment_service.py`、`artifact_transfer.py` 和窄 API helper。
- Recommendation: `add owner file`；API/DeploymentService `edit-in-place` 仅限薄接线。

Major Complexity Alert:

- Artifact: `backend/app/api/services.py`（952 行）。
- Why it is materially oversized: 同时承载 CRUD、生命周期、部署、配置、回滚、晋升与查询端点。
- Why this slice cannot fully govern it: 全面拆 router 是独立重构，会扩大当前功能风险。
- Recommended follow-up: 本功能完成后单独计划拆分 service deploy/config/lifecycle routers；本计划不得顺手重构。

## File Map

### Create

- `backend/app/services/artifact_deployment_service.py` — 兼容校验、placement/runtime 解析和直发执行 owner。
- `backend/app/adapters/artifact_transfer.py` — `ArtifactTransfer` protocol 与 SSH/SFTP 实现。
- `backend/tests/unit/test_artifact_transfer.py` — SFTP 上传、错误和路径行为。
- `backend/tests/integration/test_artifact_deployment_service.py` — 三 runtime 与错误路径。
- `backend/tests/unit/test_deploy_request_schema.py` — CI/artifact 请求互斥契约。

### Modify

- `backend/app/schemas/service.py` — `artifact_id` 和条件必填规则。
- `backend/app/schemas/deployment.py` — 输出 `artifact_id`。
- `backend/app/services/artifact_repository.py` — `get_artifact`。
- `backend/app/services/deployment_repository.py` — `create(..., artifact_id=...)`。
- `backend/app/services/executor_factory.py` — 提取共享 `SSHTarget` 构造和 transfer factory。
- `backend/app/services/deployment_service.py` — artifact 分支和状态落库薄接线。
- `backend/app/api/deps.py` — artifact deploy service provider。
- `backend/app/api/services.py` — provider 条件、artifact 预检、门禁、审批 payload、task/audit。
- `backend/app/api/approvals.py` — 批准后恢复 artifact 请求和依赖。
- `backend/tests/integration/test_build_repositories.py` — artifact get 行为。
- `backend/tests/integration/test_deployment_repository.py` — artifact_id 持久化。
- `backend/tests/integration/test_deployment_service.py` — artifact 编排和旧 CI 回归。
- `backend/tests/integration/test_deploy_api.py` — artifact API、门禁和 provider 行为。
- `backend/tests/integration/test_approval_flow_api.py` — artifact approval payload/执行。
- `frontend/src/api/deployments.ts` — artifact deploy body 类型。
- `frontend/src/pages/BuildsPage.tsx` — artifact 行部署操作。
- `frontend/tests/integration/BuildsPage.test.tsx` — 直发、审批、task 结果。
- `backend/openapi.json`、`frontend/src/api/schema.d.ts` — 生成契约。
- `docs/使用与部署.md` — artifact 直发边界和 runtime_ref 要求。

## Task 1: Add Artifact Request and Persistence Contracts

**Files:**

- Modify: `backend/app/schemas/service.py`
- Modify: `backend/app/schemas/deployment.py`
- Modify: `backend/app/services/artifact_repository.py`
- Modify: `backend/app/services/deployment_repository.py`
- Create: `backend/tests/unit/test_deploy_request_schema.py`
- Modify: `backend/tests/integration/test_build_repositories.py`
- Modify: `backend/tests/integration/test_deployment_repository.py`

**Why:** 后续编排必须有单一、可验证的 artifact 请求和持久化契约。

**Change Necessity:** 文档无法让 API 接收 artifact，也无法保存 artifact_id；最小边界是 schema + repository，不触 runtime。

**Impact / Compatibility:** `version` 类型改为可空，但 model validator 保证 CI 模式仍必填；旧合法请求不变。

**Target contract:**

```python
class DeployRequestBody(BaseModel):
    version: str | None = Field(default=None, max_length=128)
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = Field(default=None, max_length=64)
    artifact_id: str | None = Field(default=None, min_length=32, max_length=32)

    @model_validator(mode="after")
    def require_version_or_artifact(self) -> "DeployRequestBody":
        if self.artifact_id is None and not self.version:
            raise ValueError("CI 部署需 version")
        return self
```

- [ ] **Write test:** 添加 schema 测试覆盖 `{version}`、`{artifact_id}` 通过，空 body 失败；repository 测试覆盖 missing artifact 404 和 deployment artifact_id 持久化。
- [ ] **Verify RED:** `cd backend && uv run pytest tests/unit/test_deploy_request_schema.py tests/integration/test_build_repositories.py tests/integration/test_deployment_repository.py -q`；预期因字段/方法/参数缺失失败。
- [ ] **Minimal code:** 实现上述 schema；`ArtifactRepository.get_artifact(id)`；`DeploymentRepository.create(..., artifact_id=None)`；`DeploymentOut.artifact_id`。
- [ ] **Verify GREEN:** 重跑 RED 命令并执行 `uv run ruff check app/schemas app/services/artifact_repository.py app/services/deployment_repository.py tests/unit/test_deploy_request_schema.py`；预期通过。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(deploy): add artifact request contract"`。

## Task 2: Add Narrow SSH/SFTP Artifact Transfer

**Files:**

- Create: `backend/app/adapters/artifact_transfer.py`
- Modify: `backend/app/services/executor_factory.py`
- Create: `backend/tests/unit/test_artifact_transfer.py`

**Why:** generic artifact 位于控制面本地，systemd 目标机必须先收到 tar。

**Change Necessity:** shell 命令不能让远端读取本地路径；最小边界是窄上传协议和 SSH 实现，不扩大 `Executor` ABC。

**Impact / Compatibility:** 现有 executor 构造行为不变；只把 SSHTarget 组装提取为共享 helper。

**Target contract:**

```python
class ArtifactTransfer(Protocol):
    async def upload(self, local_path: str, remote_path: str) -> None: ...

class SshArtifactTransfer:
    def __init__(self, target: SSHTarget, secrets: SecretStore, *, connector=None): ...
    async def upload(self, local_path: str, remote_path: str) -> None: ...
```

实现必须校验本地文件存在、经 AsyncSSH SFTP 创建远端父目录并上传；连接/上传异常翻译为 `AppError("artifact_upload_failed", ..., 502)`。

- [ ] **Write test:** fake connector/SFTP 记录 `makedirs` 和 `put`；覆盖成功、文件不存在 404、SFTP 异常 502、密码/密钥参数来自共享 SSHTarget。
- [ ] **Verify RED:** `cd backend && uv run pytest tests/unit/test_artifact_transfer.py -q`；预期 import 失败。
- [ ] **Minimal code:** 新建 protocol/实现；在 `executor_factory.py` 提取 `build_ssh_target_for_server()` 并新增 `build_artifact_transfer_for_server()`，Agent 返回明确 `artifact_transfer_not_supported`。
- [ ] **Verify GREEN:** `uv run pytest tests/unit/test_artifact_transfer.py tests/unit/test_executor.py tests/unit/test_ssh_executor.py -q && uv run ruff check app/adapters/artifact_transfer.py app/services/executor_factory.py tests/unit/test_artifact_transfer.py`。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(deploy): add ssh artifact transfer"`。

## Task 3: Implement ArtifactDeploymentService Runtime Owner

**Files:**

- Create: `backend/app/services/artifact_deployment_service.py`
- Create: `backend/tests/integration/test_artifact_deployment_service.py`

**Why:** 三 runtime 的类型映射、目标解析和执行必须只有一个 owner。

**Change Necessity:** runtime 原语存在但无生产调用方；最小边界是独立服务，复用 repository、factory 和 adapters。

**Impact / Compatibility:** 不触 CI、deployment 状态或 API；该 service 只执行批准后的 artifact input。

**Target contract:**

```python
@dataclass(frozen=True)
class ArtifactDeployInput:
    service_id: str
    artifact_id: str
    version: str | None
    git_sha: str | None
    uri: str
    registry_type: ArtifactRegistryType

class ArtifactDeploymentService:
    async def resolve(self, service_id: str, artifact_id: str) -> ArtifactDeployInput: ...
    async def deploy(self, service_id: str, artifact_id: str) -> ArtifactDeployInput: ...
```

规则：same-service；generic→systemd；docker→docker/k8s；无 placement 409；systemd 上传 `/tmp/axon-artifacts/<id>.tar.gz` 后 deploy 并 `rm -f`；Docker 顺序部署所有 placement；K8s 单次 patch；任何失败停止并上抛。

- [ ] **Write test:** 覆盖 resolve 404/跨服务/类型不匹配；systemd 上传→deploy→cleanup；上传失败无 deploy；Docker 多 placement 顺序和首错停止；K8s image patch；缺 runtime_ref；无 placement。
- [ ] **Verify RED:** `cd backend && uv run pytest tests/integration/test_artifact_deployment_service.py -q`；预期 service 缺失失败。
- [ ] **Minimal code:** 注入 DB、SecretStore、connector、agent registry、k8s factory、executor/transfer factories；实现唯一类型映射与 runtime_ref 解析。
- [ ] **Verify GREEN:** `uv run pytest tests/integration/test_artifact_deployment_service.py tests/unit/test_runtime_deploy.py -q && uv run ruff check app/services/artifact_deployment_service.py tests/integration/test_artifact_deployment_service.py`。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(deploy): execute artifacts across runtimes"`。

## Task 4: Orchestrate Direct Artifact Deployments

**Files:**

- Modify: `backend/app/services/deployment_service.py`
- Modify: `backend/tests/integration/test_deployment_service.py`

**Why:** artifact 执行必须进入现有 task/deployment 状态机和追溯链。

**Change Necessity:** 独立 runtime service 不会自动创建 deployment 或落终态；最小边界是 DeploymentService 的一个显式模式分支。

**Impact / Compatibility:** `DeployRequest` 增加 `artifact_id`；CI 分支保持原代码路径；artifact 分支不取 pipeline adapter、不执行 release strategy。

**Target contract:**

```python
@dataclass(frozen=True)
class DeployRequest:
    version: str | None
    strategy: DeploymentStrategy = DeploymentStrategy.ROLLING
    git_sha: str | None = None
    artifact_id: str | None = None
```

构造器新增可选 `artifact_deployer`。`_execute()` 按 artifact_id 分派到 `_execute_artifact()`；后者先 resolve 元数据、创建带 artifact_id/URI 的 running deployment，再调用 deployer，成功后由 `run_deploy()` 统一落 success。

- [ ] **Write test:** artifact 成功保存全部关联字段且 CI fake 未调用；artifact runtime 失败落 deployment/task failed；旧 CI 成功/失败和 rollout tests 原样保留。
- [ ] **Verify RED:** `cd backend && uv run pytest tests/integration/test_deployment_service.py -q`；预期新测试因参数/分支缺失失败。
- [ ] **Minimal code:** 添加请求字段、注入点和 `_execute_artifact`；保持健康检查使用现有 service 配置；不复制状态流转代码。
- [ ] **Verify GREEN:** `uv run pytest tests/integration/test_deployment_service.py tests/integration/test_deploy_auto_rollback_on_unhealthy.py tests/integration/test_deploy_scan_backfill.py -q && uv run ruff check app/services/deployment_service.py tests/integration/test_deployment_service.py`。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(deploy): orchestrate direct artifact deployments"`。

## Task 5: Wire API, Quality Gate, Approval, and Dependencies

**Files:**

- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/services.py`
- Modify: `backend/app/api/approvals.py`
- Modify: `backend/tests/integration/test_deploy_api.py`
- Modify: `backend/tests/integration/test_approval_flow_api.py`

**Why:** 用户请求必须在远端动作前经过权限、artifact 校验、质量门禁和生产审批。

**Change Necessity:** 当前 API 在加载 service/artifact 前强制 CI provider，且 approval payload 无 artifact_id；配置或文档不能修复。

**Impact / Compatibility:** provider 仅在 CI 模式必需；artifact 模式沿用同一 endpoint/permission/task type；旧审批 payload 继续可解析。

**Required flow:**

```text
load service -> permission -> resolve artifact metadata (when present)
-> validate request metadata/rolling -> quality gate using resolved git_sha
-> approval payload -> _start_deploy -> background DeploymentService
```

错误码严格采用规格：`artifact_not_found`、`artifact_service_mismatch`、`artifact_runtime_mismatch`、`artifact_metadata_mismatch`、`artifact_strategy_not_implemented`、`artifact_transfer_not_supported`。

- [ ] **Write test:** artifact 模式无 CI provider 仍 202；CI 模式无 provider 501；metadata mismatch 409；critical gate 422；prod approval payload 含 artifact_id；批准后 direct deployer 被调用；发起人不可自批。
- [ ] **Verify RED:** `cd backend && uv run pytest tests/integration/test_deploy_api.py tests/integration/test_approval_flow_api.py -q`；预期 artifact cases 失败。
- [ ] **Minimal code:** `get_artifact_deployment_service` 复用 app.state 依赖；抽出 API 薄 helper 解析 artifact；更新 `_start_deploy` task/audit payload 和 approval 恢复逻辑。
- [ ] **Verify GREEN:** 重跑 RED 命令，再执行 `uv run pytest tests/integration/test_deploy_gate_api.py tests/integration/test_key_operation_notify.py -q && uv run ruff check app/api/deps.py app/api/services.py app/api/approvals.py`。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(api): wire artifact deployment flow"`。

## Task 6: Add Artifact Row Deployment UX

**Files:**

- Modify: `frontend/src/api/deployments.ts`
- Modify: `frontend/src/pages/BuildsPage.tsx`
- Modify: `frontend/tests/integration/BuildsPage.test.tsx`

**Why:** 用户选择明确 artifact 的入口必须位于 artifact 行，不能与服务页 CI 部署混淆。

**Change Necessity:** 后端能力没有用户入口；最小边界是现有构建页的一项行操作和复用 API/task polling。

**Impact / Compatibility:** 服务页和部署页 UI 不变；按钮只在当前 service 的 artifact 表中出现。

**Target API type:**

```ts
export interface DeployBody {
  version?: string;
  strategy: DeploymentStrategy;
  git_sha?: string;
  artifact_id?: string;
}
```

点击“部署”后用 Ant Design confirmation 展示 service/env/runtime/artifact/version/URI；确认后调用 `deployService(service.id, { artifact_id: artifact.id, strategy: "rolling" })`。pending approval 显示既有审批提示；task success/failed/unknown 使用现有轮询语义。

- [ ] **Write test:** mock `deployService`/`pollTaskUntilDone`；覆盖按钮展示、确认 payload、pending approval 不轮询、success 刷新、failed 错误提示。
- [ ] **Verify RED:** `cd frontend && npm run test -- BuildsPage.test.tsx`；预期找不到部署按钮或调用。
- [ ] **Minimal code:** 扩展 DeployBody；在 artifact columns 增加单一操作；复用 `isPendingApproval` 和 task polling；保持稳定列宽避免布局跳动。
- [ ] **Verify GREEN:** `npm run test -- BuildsPage.test.tsx && npm run lint && npm run build`；预期通过，允许已有 bundle-size warning 但不得新增错误。
- [ ] **Commit:** `git add <上述文件> && git commit -m "feat(frontend): deploy artifacts from build page"`。

## Task 7: Regenerate Contract, Document Operations, and Run Full Verification

**Files:**

- Modify generated: `backend/openapi.json`
- Modify generated: `frontend/src/api/schema.d.ts`
- Modify: `docs/使用与部署.md`
- Review: all files changed by Tasks 1-6

**Why:** API 契约和运维要求必须与实际功能一致，完成前需要全链回归证据。

**Change Necessity:** schema 变化会让已生成 OpenAPI/TS 类型过期；systemd runtime_ref 和 SFTP 边界需要用户文档。

**Impact / Compatibility:** 生成 diff 只允许 `artifact_id`、可选 version 和 DeploymentOut artifact_id；不得出现无关 schema churn。

- [ ] **Write verification assertions:** 在文档明确类型映射、rolling-only、同服务、systemd `deploy_path`、SSH-only 和失败语义；列出 smoke steps，不加入假数据路径。
- [ ] **Verify pre-generation drift:** `make gen-api && git diff -- backend/openapi.json frontend/src/api/schema.d.ts`；人工确认仅批准契约变化。
- [ ] **Minimal docs/code:** 更新使用文档；若生成结果出现无关变化，定位 source schema 或工具版本，不手改生成文件掩盖差异。
- [ ] **Verify full GREEN:** 依次运行 backend targeted、backend full/static、frontend lint/test/build、`git diff --check`；正常主机补跑 gRPC E2E 和 Go Agent `go test ./...`。
- [ ] **Commit:** `git add backend/openapi.json frontend/src/api/schema.d.ts docs/使用与部署.md && git commit -m "docs: publish artifact deployment contract"`。

## Final Verification Commands

```bash
cd backend
uv run pytest \
  tests/unit/test_deploy_request_schema.py \
  tests/unit/test_artifact_transfer.py \
  tests/integration/test_artifact_deployment_service.py \
  tests/integration/test_deployment_service.py \
  tests/integration/test_deploy_api.py \
  tests/integration/test_approval_flow_api.py -q
uv run ruff check app tests
uv run black --check \
  app/adapters/artifact_transfer.py \
  app/services/artifact_deployment_service.py \
  app/services/artifact_repository.py \
  app/services/deployment_repository.py \
  app/services/deployment_service.py \
  app/api/deps.py app/api/services.py app/api/approvals.py \
  app/schemas/service.py app/schemas/deployment.py \
  tests/unit/test_deploy_request_schema.py \
  tests/unit/test_artifact_transfer.py \
  tests/integration/test_artifact_deployment_service.py
uv run pytest tests --ignore=tests/integration/test_agent_grpc_e2e.py -q

cd ../frontend
npm run lint
npm run test
npm run build

cd ..
git diff --check
git status --short --branch
```

## Risks

- Partial multi-placement deployment: first failure leaves earlier targets updated；必须整体落 failed 并返回失败 target，不假装原子性。
- Local artifact disappearance: upload 前明确 404/409，不创建远端目录或 runtime 副作用。
- Registry credentials: 本功能只消费已可拉取 URI，不把 registry credential 注入命令或日志。
- Cleanup failure: 上传文件清理失败记录 warning，不覆盖已完成 deploy；残留路径可由固定前缀治理。
- API size: 只增加 helper 调用，禁止顺手拆分或继续嵌入 runtime 分支。
- Generated contract drift: 生成工具版本不一致时停止并定位，不接受无关大 diff。

## Repair Track

- Root cause: artifact 已生成并有 runtime 原语，但没有 canonical owner 把 artifact 解析、传输和部署接入状态编排。
- Canonical owner: 新 `ArtifactDeploymentService`。
- Minimal sufficient repair: 新 owner + 窄 transfer + 现有 DeploymentService/API/UI 薄接线。
- Compatibility boundary: 旧 CI 路径和所有治理控制不变。
- Verification: artifact direct producer/consumer tests + CI regression + UI/API contract。

## Retirement Track

- Old owner / fallback: 不存在可用的旧 artifact 直发 owner；`SSHExecutor.deploy()` 的通用 `deploy <artifact>` 占位不得成为新主路径。
- Active status: runtime 直发目前仅单测可达，生产不可达。
- Action: 新 service 直接调用具体 runtime adapter；不新增 fallback 到 CI 或通用 deploy shell。
- Retention reason: 保留 `Executor.deploy()` 只为现有接口兼容，当前计划不扩大其用途。
- Deletion trigger: 后续全仓确认没有生产 consumer 后，单独评估从 Executor ABC 退休通用 deploy。
- Lingering reference check: 完成时 `rg -n "\.deploy\(|DeploySpec" backend/app backend/tests` 核对 owner 和调用方向。

## ADR / Baseline Sync Signal

- ADR trigger: yes，新 artifact deployment owner、窄 transfer 协议和 direct-vs-CI contract 属持久架构边界。
- Alternatives preserved: rollout provider 扩张、API 内分支、独立 service。
- Completion action: 实施通过后运行 ADR Auto Backfill；建议创建 ADR 时同步初始 baseline 的 ownership snapshot。
- Authority boundary: 本计划不提前接受 ADR，测试通过也不等于需求已被最终验收。

# Artifact Direct Deployment Design

Date: `2026-07-20`
Status: `approved design; awaiting written-spec review`

## 1. TaskIntentDraft

- Outcome: 用户在构建页选择一个明确 artifact，并把它真实部署到所属 service 的 runtime。
- Goal: 闭合“代码 → 构建 → artifact → runtime → deployment”可追溯链路，同时保留旧 CI 部署模式。
- Success evidence: artifact 模式的 API、审批、质量门禁、三 runtime 主路径、错误路径和前端操作均有自动化测试；旧 CI 回归通过。
- Stop condition: 规格获用户确认并转入实施计划；本规格阶段不修改业务代码。
- Non-goals: 跨服务/跨环境直发、自动挑选最新 artifact、Agent systemd 文件上传、高级策略直发、制品 CDN。
- Scope risk: systemd 需要本地文件上传；直接部署与现有 release strategy 容易职责重叠。

## 2. BaselineReadSetHint

- `README.md`
- `docs/统一运维控制面-设计文档.md`
- `docs/统一运维控制面-任务拆分.md`
- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- 当前 artifact、build、deployment、approval、runtime 与 frontend build 页面实现。

## 3. BaselineUsageDraft

- Required baseline refs: 项目 README、设计文档、任务拆分、初始双基线。
- Delivered context refs: 用户已确认的四项产品选择和 systemd SFTP 传输选择。
- Acknowledged before plan refs: 构建/制品模型、部署编排、runtime adapter、审批与质量门禁。
- Cited in design refs: 本规格第 2 节列出的全部来源。
- Missing refs: 无已接受 ADR；实施完成后再判断是否需要 ADR backfill。
- Decision: `continue`。

## 4. Requirement Ready Check

- Requirement source refs: 当前对话的逐项选择与批准、项目 README/设计文档。
- Goals and scope refs: 本规格第 1 节。
- User / scenario refs: 运维人员从构建页选择所属 service 的一个 artifact 并部署。
- Requirement item refs: 第 6 至第 13 节。
- Acceptance / verification criteria refs: 第 14 节。
- Open blocker questions: 无。
- Decision: `ready`。

## 5. ImpactStatementDraft

- Affected layers: API schema、部署编排、artifact repository、runtime 部署 provider、SSH 文件传输、审批 payload、部署历史输出、前端构建页。
- Canonical owners: `DeploymentService` 管状态；新 `ArtifactDeploymentService` 管 artifact 到 runtime 的执行；runtime adapter 管命令翻译。
- Invariants: artifact 必须属于 service；类型必须兼容；生产控制不绕过；失败不得假成功。
- Compatibility: 未传 `artifact_id` 的旧部署请求行为不变。
- Non-goals: 不改变 promotion、webhook、CI adapter 或高级策略实现。

## 6. Confirmed Product Decisions

1. 部署请求显式传入 `artifact_id`；不自动按 version 查找。
2. artifact 只能部署到其所属的同一个 `service_id`。
3. `generic` 只兼容 `systemd`；`docker` 兼容 `docker` 和 `k8s`。
4. 类型不匹配在远端动作前返回明确错误。
5. 前端入口位于构建页 artifact 行；服务页原入口继续表示 CI 部署。
6. systemd generic artifact 通过 SSH/SFTP 上传到目标机后部署。

## 7. Existence Check

- Proposed new surface: `ArtifactDeploymentService` 与窄接口 `ArtifactTransfer`。
- Existing owner / reuse candidate: `DeploymentService`、`rollout_provider`、`Executor`。
- Why existing surface is insufficient: `DeploymentService` 不应承担连接和 runtime 细节；`rollout_provider` 只拥有策略铺开；扩大 `Executor` 会强迫 Local/Agent/K8s 实现无关文件上传。
- Creation proof: systemd 本地 artifact 需要独立传输能力，且 Docker/K8s/systemd 需要统一兼容校验和 target 解析 owner。
- Entropy / retirement impact: 新服务替代把分支散落到 API/DeploymentService；不保留第二套 artifact 部署规则。
- Decision: `add-with-proof`。

## 8. Options Considered

### Option A — 扩展 rollout provider

- 优点: 文件较少，可复用 placement 解析。
- 缺点: 把制品传输、runtime 部署和发布策略混为一个 owner；直发后容易重复 restart/scale。

### Option B — 独立 ArtifactDeploymentService（采用）

- 优点: owner 清晰；复用 runtime adapter、executor factory、SecretStore；可单独测试传输和兼容规则。
- 缺点: 增加一个服务和一个窄传输协议。

### Option C — API 内直接分支

- 优点: 文本改动最少。
- 缺点: 审批、回滚和后台任务难复用，业务规则落错层。

## 9. API Contract

`DeployRequestBody` 增加：

```text
artifact_id: str | None
version: str | None
strategy: DeploymentStrategy = rolling
git_sha: str | None
```

规则：

- `artifact_id` 缺省：`version` 必填，保持现有 CI 部署。
- `artifact_id` 存在：从 artifact 派生 `version`、`git_sha` 和 `uri`。
- 客户端同时传入 version/git_sha 时，若与 artifact 不一致则返回 `409 artifact_metadata_mismatch`，不静默覆盖。
- artifact 直发首版只支持 `rolling`；其他 strategy 返回 `501 artifact_strategy_not_implemented`。CI 模式策略行为不变。
- `DeploymentOut` 增加 `artifact_id`。

## 10. Orchestration Flow

1. API 加载 service 和 artifact，执行 service deploy 权限校验。
2. 校验 artifact 存在、属于 service，并读取 registry type。
3. 执行类型/runtime 兼容检查。
4. 用 artifact.git_sha 执行现有质量门禁。
5. 生产环境沿用现有审批流程；approval payload 必须保存 `artifact_id` 与 strategy。
6. 创建 deploy task 与审计记录，审计 `after` 标注 `mode=artifact`。
7. `DeploymentService` 创建 running deployment，写入 `artifact_id`、`artifact=uri`、version、git_sha、previous 和 scan result。
8. 调用注入的 `ArtifactDeploymentService.deploy(service, artifact)`。
9. 所有目标成功后 deployment/task 落 success；任一目标失败则二者落 failed，并保留明确错误。
10. artifact 模式不触发 CI，也不再调用 `execute_release_strategy`，避免重复重启。

## 11. Runtime Execution

### 11.1 systemd + generic

- 仅支持 SSH placement；Agent placement 返回 `501 artifact_transfer_not_supported`。
- `ArtifactTransfer` 通过 SFTP 把本地 tar 上传到 `/tmp/axon-artifacts/<artifact_id>.tar.gz`。
- `SystemdRuntime.deploy()` 接收远端临时路径、`unit_name` 和 `deploy_path`。
- `unit_name`、`deploy_path` 缺失时在远端动作前拒绝。
- deploy 完成或失败后尝试删除临时文件；清理失败写结构化 warning，不覆盖部署主结论。

### 11.2 docker + docker artifact

- 对 service 的每个 placement 构造现有 Executor 和 `DockerRuntime`。
- `image=artifact.uri`，`container_name`、可选 `env`/`ports` 来自 `runtime_ref`。
- placement 按稳定顺序逐个部署；首个失败停止后续目标并把整体部署标记 failed。

### 11.3 k8s + docker artifact

- 使用现有 `k8s_api_factory` 创建 `K8sRuntime`。
- `image=artifact.uri`，`namespace/workload` 来自 `runtime_ref`。
- 通过 JSON Patch 替换首个容器镜像，由 Deployment 自身滚动策略完成 Pod 替换。

## 12. Data and Source of Truth

- artifact 身份、类型、URI、version 和 git SHA 的 source of truth 是 `Artifact` 与其 `ArtifactRegistry`。
- 请求中的 version/git SHA 不是 artifact 模式的权威值。
- deployment 保存 `artifact_id` 和 `artifact` 字符串快照，既支持追溯也兼容旧查询。
- 不新增数据库列；现有 `deployments.artifact_id` 直接启用。

## 13. Frontend Behavior

- `BuildsPage` 的 artifact 表格增加“部署”操作。
- 点击后显示确认信息：service、env、runtime、artifact name/version/URI。
- 提交 `{artifact_id, strategy: "rolling"}` 到现有 service deploy endpoint。
- 复用 task 轮询和 pending approval 提示。
- 成功后刷新 builds/artifacts 与 deployment 相关查询；不在服务页增加第二个重复入口。

## 14. Error Contract and Acceptance Criteria

### 14.1 Required errors

- `artifact_not_found` — 404。
- `artifact_service_mismatch` — 409。
- `artifact_runtime_mismatch` — 409。
- `artifact_metadata_mismatch` — 409。
- `artifact_strategy_not_implemented` — 501。
- `artifact_transfer_not_supported` — 501。
- 上传、runtime 或 placement 执行失败 — task/deployment failed，错误不吞掉。

### 14.2 Automated acceptance

1. API: artifact 模式创建 task；CI provider 不被调用。
2. Compatibility: 旧 CI 部署请求及测试保持通过。
3. Security: 权限、生产审批、四眼原则、质量门禁均覆盖 artifact 模式。
4. Persistence: deployment 正确保存 artifact_id、URI、version、git SHA、previous 和状态。
5. systemd: SFTP 上传 → 解包/重启 → 清理顺序可验证；上传失败无 runtime 动作。
6. docker: 多 placement 顺序执行，首个失败停止并落失败。
7. k8s: patch 使用 artifact image URI，错误翻译保持一致。
8. Frontend: artifact 行部署、审批提示、task 成功/失败反馈有集成测试。
9. Static/regression: targeted tests、全后端测试（环境允许范围）、前端 lint/test/build。

## 15. Product Risk Lens

- Value: 首次把控制面自建构建产物真实送达 runtime，完成核心价值链闭环。
- Non-goals: 不把首版扩大成通用发布平台或跨环境替代 promotion。
- Trade-offs: 首版 rolling-only 和 systemd SSH-only，以明确失败换取真实语义。
- Decision needed: 用户审阅本书面规格后批准进入实施计划。

## 16. Architecture Integrity Lens

- Invariant: artifact 身份与类型只有一个 owner，远端动作前完成全部可静态验证。
- Canonical owner / contract: `ArtifactDeploymentService` 拥有 artifact→runtime 执行；runtime adapter 只翻译动作；`DeploymentService` 只管状态编排。
- Responsibility overlap: 不把 artifact 规则写入 API 或 release strategy。
- Higher-level simplification: 复用现有 task、approval、quality gate、runtime adapter 与 executor factory。
- Retirement / falsifier: 若实施仍在 API 或 rollout provider 出现第二套类型映射，设计失败并应回收。
- Verdict: `aligned`。

## 17. Baseline Role Alignment

- Product / Requirement Baseline: 构建到部署的可追溯闭环、审批和兼容要求。
- Architecture / Runtime Boundary Baseline: 部署编排、runtime adapter、artifact repository、release strategy 的既有 owner。
- Result: `aligned`。
- scope: `both`。
- Next action: 用户批准本规格后进入 writing-plans。

## 18. Complexity Budget

- Artifact class: cross-module feature / runtime integration。
- Target files / artifacts: deployment schema/service/API、artifact repository、新 artifact deploy service/transfer、approval、frontend build API/page、tests。
- Current pressure: `DeploymentService` 和 services API 已较大，不宜继续塞 runtime 分支。
- Projected post-change pressure: 独立 owner 后为 `within-budget`；直接写入现有 owner 则 `at-risk`。
- Planned governance: 新服务保持窄职责；API 只做 transport/auth；共享规则只存在一份。

Plan-Time Complexity Check:

- Better file boundary: 新 `artifact_deployment_service.py` 和窄 transfer protocol/实现。
- Recommendation: `add owner file`，不扩张 `release_strategy.py`。

## 19. ADR Signal

- Trigger: yes。
- Durable surface: 新 artifact deployment owner、文件传输协议、部署请求契约和 runtime-ready boundary。
- Alternatives: 扩展 rollout provider、API 内分支、独立服务。
- Expected follow-up: 实施验证后判断创建 ADR，不能用未实施设计提前宣称接受。

# Artifact Direct Deployment — Intent

Date: `2026-07-20`

## TaskIntentDraft

- Requested outcome: 从构建页选择明确 artifact，并真实部署到所属 service 的 runtime。
- Goal: 执行批准的 Design Spec 和 Implementation Plan，完成三 runtime 主路径及治理闭环。
- Success evidence: 计划 7 个任务逐项通过 RED/GREEN、规格审查、代码质量审查和最终回归。
- Stop condition: `done | blocked | needs-verification | scope-exceeded`。
- Non-goals: 跨服务/跨环境直发、自动挑最新 artifact、Agent systemd 上传、高级策略直发、顺手拆分大型 router。
- Risk hints: systemd SFTP、部分多 placement 部署、旧 CI 兼容、审批/门禁不绕过。

## BaselineReadSetHint

- `docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md`
- `docs/aegis/plans/2026-07-20-artifact-direct-deployment.md`
- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `README.md`

## BaselineUsageDraft

- Required refs: approved spec、implementation plan、initial baseline。
- Acknowledged refs: artifact/build/deployment models、API/approval、runtime adapters、executor factory、BuildsPage。
- Cited refs: 本 work record 与每个 SubagentContextPacket。
- Missing refs: 无；ADR 在完成候选阶段判断。
- Decision: `continue`。

## ImpactStatementDraft

- Layers: backend schema/repository/service/API/approval/adapter、frontend API/page、generated contract、docs。
- Owners: ArtifactDeploymentService、DeploymentService、runtime adapters、ArtifactTransfer。
- Compatibility: 无 artifact_id 的 CI 路径保持不变。
- Non-edits: migrations、promotion、webhook、advanced release strategy。

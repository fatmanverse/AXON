# Task Intent

- Requested outcome: 完成定向回滚、artifact 原生回滚、部署历史制品展示与同 service 部署互斥。
- Scope: backend schema/repository/service/API/migration/tests，frontend API/page/tests，生成契约和使用文档。
- Non-goals: 排队、取消、自动重试、stale task 治理、跨 service/env 回滚。
- Risk hints: 持久化唯一约束、approval 状态原子性、artifact/CI owner 边界、既有脏工作区。
- ArchitectureReviewRequired: `yes`。

## BaselineReadSetHint

- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md`
- `docs/aegis/specs/2026-07-23-targeted-rollback-and-deploy-exclusivity-design.md`

## BaselineUsageDraft

- Required refs: 上述三个文件与现有实现/测试。
- Acknowledged refs: 上述三个文件已读取。
- Cited refs: Design Spec 与实施计划。
- Missing refs: 无。
- Decision: `continue`。

## ImpactStatementDraft

- Canonical owners: DB+TaskRepository 互斥；DeploymentRepository/Service 目标与状态；ArtifactDeploymentService runtime。
- Compatibility: 旧无 body rollback 沿 previous；CI 与非部署 task 不变。
- Stop: 计划全部任务完成并有验证证据，或明确外部环境阻塞项。

# 完整平台生产化设计

Date: `2026-07-23`
Status: `approved design`

## 1. Goal

将 Axon 统一运维控制面从测试/预发可用提升到完整生产平台边界：

- 被管运行时同时支持 `systemd`、Docker、Kubernetes。
- 目标机同时支持 SSH 和 Agent 接入。
- 部署、回滚、晋升、配置下发、构建和告警联动保持现有 task、审批、权限、质量门禁、审计和可追溯链路。
- 控制面可部署在 Docker Compose 单机、VM/systemd 和 Kubernetes 高可用环境。
- rolling、recreate、canary、blue-green 四种策略均有明确的真实 provider；缺 provider 时显式失败，不静默降级。

## 2. Baseline and Authority

- Product / Requirement baseline: `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- Architecture governance: `docs/aegis/BASELINE-GOVERNANCE.md`
- Existing rollback and deployment ownership: `docs/aegis/adr/ADR-0001-targeted-rollback-and-deployment-exclusivity.md`
- Runtime and delivery requirements: `README.md`, `docs/统一运维控制面-设计文档.md`, `docs/统一运维控制面-任务拆分.md`

The existing baseline's high-availability and advanced-strategy non-goals are superseded for this workstream only by this approved design. Existing API, migration revision IDs, task semantics and deployment ownership remain compatibility constraints.

## 3. Requirement Ready Check

- Requirement source refs: this approved design and the baseline refs above.
- User/scenario: production operators manage mixed runtime fleets from one control plane.
- Acceptance: all runtime/provider/deployment profiles have executable tests and a real-environment smoke path; production security and recovery gates pass.
- Decision: `ready`.

## 4. Scope

### 4.1 Control-plane deployment profiles

| Profile | Runtime | Intended use | Required properties |
|---|---|---|---|
| Compose production | Docker Compose | small single-site production and pre-production | external PostgreSQL/Redis, secret injection, health gates, resource limits, backup/restore command |
| VM production | systemd + reverse proxy | traditional VM production | hardened units, TLS termination, worker/beat separation, upgrade and rollback script |
| Kubernetes production | Kubernetes | multi-replica HA | API/worker replicas, singleton beat, external PostgreSQL/Redis, Ingress TLS, PodDisruptionBudget, probes and rollout policy |

All profiles use the same backend/frontend artifacts and the same Alembic migration chain.

### 4.2 Runtime and access matrix

| Target runtime | SSH | Agent | Artifact deploy | Lifecycle/config | Strategies |
|---|---:|---:|---:|---:|---|
| systemd | yes | yes | generic archive transfer | yes | rolling/recreate; canary/blue-green via LB provider |
| Docker | yes | yes | image or archive as configured | yes | rolling/recreate; canary/blue-green via LB provider |
| Kubernetes | API client | Agent only for explicitly supported host actions | image URI | API-native | rolling/recreate; canary/blue-green via Argo provider |

The runtime adapter is the canonical owner of runtime-specific validation and execution. API handlers do not duplicate runtime rules.

### 4.3 Security and identity

- OIDC/OAuth2 is the production authentication integration; local JWT login remains a break-glass path with explicit enablement.
- Local identity management includes password change, token/session revocation, failed-login lockout and administrator recovery.
- Agent connections use mTLS, registered Agent identity, certificate rotation and revocation checks.
- Production startup rejects default or missing JWT, master, webhook and seed credentials.
- Secrets remain in the configured secret backend and are never persisted as plaintext application data.

### 4.4 Distributed runtime

- Redis is the shared owner for rate-limit buckets, WebSocket fan-out and task/deployment notifications in multi-replica mode.
- Database constraints remain the source of truth for deployment exclusivity and idempotency.
- API is stateless; Celery worker is horizontally scalable; beat is singleton with an explicit lease/lock.

### 4.5 Build and CI

- Build nodes are registered with capability labels and max-concurrency leases.
- Local node remains a valid profile; SSH build nodes are scheduled by capability and availability.
- API and worker resolve the same serializable CI provider factory from configuration.
- Webhook events remain the fast path; periodic reconciliation is a real provider-backed fallback and is observable when skipped or failed.

### 4.6 Release strategies

- rolling and recreate keep their current semantics.
- Kubernetes canary/blue-green use an Argo Rollouts provider with health gates and abort/rollback handling.
- systemd/Docker canary/blue-green use a LoadBalancer provider with weight or upstream switching and an explicit rollback path.
- A strategy without a configured provider returns a typed `501`/`409` error and creates no false-success task.

### 4.7 Operations and recovery

- PostgreSQL backup and restore are executable and tested against a disposable database.
- Upgrade runs migrations as a separate step, validates health, and has a tested rollback/restore path for failed upgrades.
- Prometheus retention, dashboard provisioning, alert routing and log/metric health checks are configured per deployment profile.
- Smoke tests cover real PostgreSQL, Redis, Celery, CI, at least one systemd target, one Docker target, one Kubernetes target and one Agent target.

## 5. Compatibility Boundary

The following must not regress:

1. Existing CI deployment payloads using `version` continue to work.
2. Artifact deployment continues to require explicit `artifact_id` and preserve service ownership checks.
3. Rollback without a body continues to follow `previous_deployment_id`; targeted rollback remains canonical.
4. Approval, permission, quality-gate, audit, task polling and deployment-exclusivity rules remain shared across CI and artifact paths.
5. Existing Alembic revision IDs and database state remain valid.
6. SSH-only deployments remain usable when Agent, OIDC, Argo or a LoadBalancer provider is disabled.

## 6. Architecture Invariants

- One canonical owner per concern: deployment state in `DeploymentService`, artifact runtime behavior in `ArtifactDeploymentService`, runtime translation in runtime adapters, rollout orchestration in provider-backed strategy modules, authentication in the identity layer, and distributed coordination in Redis/database primitives.
- No caller-side fallback may duplicate runtime or artifact rules.
- Partial placement failure is recorded as failed/unknown with explicit reconciliation; it is never reported as success.
- External provider absence is an explicit capability error, not a silent local emulation.
- Persistent production data is never deleted as part of automated verification.

## 7. Phased Acceptance

### Phase 0: production security baseline

Pass criteria: fail-fast production config, secret validation, secure ingress, identity lifecycle, Agent mTLS, hardened default deployments, and security regression tests.

### Phase 1: runtime and access completeness

Pass criteria: real systemd, Docker and Kubernetes smoke for deploy/rollback/config/lifecycle; SSH and Agent paths share task/audit semantics; artifact transfer works for supported target types.

### Phase 2: distributed reliability

Pass criteria: multi-replica API/worker, Redis fan-out/rate limits, singleton beat lease, provider-backed CI reconciliation, idempotency and restart recovery tests.

### Phase 3: advanced release and build scheduling

Pass criteria: BuildNode registration/scheduling/concurrency, Argo canary/blue-green, LoadBalancer canary/blue-green, health gates, abort and rollback tests.

### Phase 4: delivery and recovery gate

Pass criteria: Compose/VM/Kubernetes packaging, database backup/restore drill, upgrade rollback drill, observability checks, all automated suites and format/lint gates green, and real-environment smoke evidence recorded.

## 8. External Dependencies and Authority

- PostgreSQL and Redis production instances or managed services.
- OIDC provider and certificate authority for production identity.
- Argo Rollouts/service mesh for Kubernetes advanced strategies.
- A supported LoadBalancer API for systemd/Docker advanced strategies.
- A real CI provider and artifact registry.
- Disposable staging targets for systemd, Docker, Kubernetes and Agent smoke tests.

If any dependency is unavailable, the affected phase is `blocked` or `needs-verification`; it is not marked complete using a fake provider.

## 9. ADR and Baseline Signals

This design touches durable architecture boundaries: identity, distributed coordination, runtime adapters, external rollout providers, build scheduling and deployment packaging. Each provider or source-of-truth change requires an ADR/backfill decision and must preserve the existing rollback/exclusivity ADR boundary.

## 10. Non-goals

- No proprietary CI, IdP, service mesh or load balancer is invented in the control plane.
- No production credentials or live database are used by automated tests.
- No silent compatibility fallback is added to make an unsupported provider appear successful.
- No claim of production acceptance is made until the phase gates and real-environment evidence pass.

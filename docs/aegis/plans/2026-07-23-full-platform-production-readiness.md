# 完整平台生产化实施计划

Goal: 按 `docs/aegis/specs/2026-07-23-full-platform-production-readiness-design.md`，将 Axon 补齐为同时支持 systemd、Docker、Kubernetes 目标运行时，以及 Compose、VM/systemd、Kubernetes 控制面部署形态的生产平台。

Architecture: 保留现有 `DeploymentService`、`ArtifactDeploymentService`、runtime adapter、task/approval/audit、Alembic revision 和 API 兼容边界；新增能力只能接入现有 owner 或通过窄 provider 接口，不在 API 层复制业务规则。

Tech Stack: FastAPI/SQLAlchemy/Alembic/Celery/Redis/PostgreSQL、React/Vite、Go/gRPC Agent、Docker Compose、systemd、Kubernetes/Argo Rollouts、OIDC/OAuth2、Prometheus/Grafana/Alertmanager。

Baseline/Authority Refs:

- `docs/aegis/specs/2026-07-23-full-platform-production-readiness-design.md`
- `docs/aegis/baseline/2026-07-20-initial-baseline.md`
- `docs/aegis/BASELINE-GOVERNANCE.md`
- `docs/aegis/adr/ADR-0001-targeted-rollback-and-deployment-exclusivity.md`
- `README.md`
- `docs/统一运维控制面-设计文档.md`

Compatibility Boundary:

- Existing CI `version` deployment, explicit artifact deployment, targeted/legacy rollback, promotion, approval, webhooks, task polling, SSH runtime and Alembic revision IDs remain compatible.
- Unsupported external providers return typed capability errors and never create false-success tasks.
- Automated verification never mutates live production data.

Verification:

- Targeted unit/integration tests for each changed owner.
- Backend full suite, Ruff, Black; frontend full suite, ESLint, Prettier, typecheck/build.
- Fresh PostgreSQL/SQLite Alembic upgrade and downgrade checks.
- Docker Compose config/build checks and VM/Kubernetes manifest validation.
- Real staging smoke for CI, PostgreSQL, Redis, Celery, systemd, Docker, Kubernetes and Agent.
- Backup/restore and upgrade rollback drills.

## Scope Check

- Requirement status: `ready`, based on the approved design spec.
- Change necessity: `code-change`; security, distributed state, external adapters, runtime contracts and release packaging cannot be closed by documentation-only changes.
- Existence check: reuse existing runtime, task, approval, artifact, provider and secret owners; add only Redis coordination, identity, provider and scheduler modules where no current owner exists.
- Architecture integrity: runtime rules stay in adapters/providers, deployment state stays in `DeploymentService`, and distributed coordination has one Redis/database owner.
- Plan pressure: several core files already exceed comfortable size; new behavior is split into provider/coordination modules instead of growing API handlers.

## Phase 0: Security and Production Configuration

### Task 0.1: Production configuration fail-fast

Files: `backend/app/core/config.py`, `backend/app/main.py`, `backend/app/cli/seed.py`, `.env.example`, `docker-compose.yml`, `docs/使用与部署.md`, `backend/tests/unit/test_config.py`.

Why: current defaults permit weak credentials and an empty secret master key in production.

Change: add an explicit production validation method invoked during app startup and migration/seed entrypoints; reject default JWT, database, seed, Grafana, webhook and master key values when `YIMAI_ENV=prod`; require external secret backend or a stable master key; document a non-secret production environment contract.

Verification: write failing config tests for each rejected default, run them red, implement validation, run targeted tests green, then run the Docker Compose config check with non-production defaults and a production validation smoke.

Retirement: retire silent default acceptance in production; retain defaults only for `dev`/`test` profiles.

### Task 0.2: Identity lifecycle and OIDC boundary

Files: `backend/app/api/auth.py`, `backend/app/services/auth_service.py`, `backend/app/models/user.py`, new Alembic migration if fields are needed, frontend auth store/pages, tests.

Why: production requires account lifecycle, session revocation and an enterprise identity boundary.

Change: add session/jti revocation with Redis-backed denylist, password change and admin recovery endpoints, failed-login counters/lockout, and an OIDC provider interface with a local break-glass mode explicitly gated by configuration. Keep existing JWT response shape for compatibility.

Verification: tests cover lockout, password change, revoked token, OIDC callback failure and local-mode compatibility; run API/frontend auth integration tests and a security review of token storage.

### Task 0.3: Agent mTLS and identity

Files: `agent/main.go`, `agent/gen/agentpb/*` only if protocol metadata changes, `backend/app/services/agent_grpc_server.py`, `backend/app/core/config.py`, new certificate/identity service modules, tests and deployment docs.

Why: current Agent transport is plaintext and has no cryptographic identity.

Change: support server TLS and client certificate verification, Agent registration identity, certificate rotation/revocation and explicit insecure mode only for local development. Keep stream/task/fence semantics unchanged.

Verification: in-memory protocol tests plus real localhost TLS stream smoke; reject missing/invalid/expired certificates; confirm SSH path remains usable when Agent is disabled.

## Phase 1: Runtime and Artifact Completeness

### Task 1.1: Unified Agent artifact/config/lifecycle execution

Files: `backend/app/adapters/agent_gateway.py`, `backend/app/adapters/artifact_transfer.py`, `backend/app/services/agent_delivery_service.py`, `agent/main.go`, protocol/tests.

Why: Agent currently supports command/config primitives but artifact deployment is not a complete production path.

Change: define narrow upload, extract, lifecycle and result-ack commands; implement them in Go with path validation, bounded payloads, checksums and atomic config writes; route systemd/Docker operations through the existing executor/provider owners.

Verification: tests cover checksum mismatch, path traversal, interrupted transfer, idempotent task replay and fence rejection; real Agent smoke deploys and rolls back a disposable systemd/Docker target.

### Task 1.2: Runtime matrix contract tests

Files: runtime adapters, `ArtifactDeploymentService`, `release_strategy`, contract tests and docs.

Why: all three target runtimes must have explicit behavior instead of accidental fallback.

Change: add shared contract fixtures for SSH and Agent systemd/Docker, and Kubernetes API deploy/config/lifecycle; keep Kubernetes API-native behavior and typed unsupported errors.

Verification: matrix test suite covers deploy, rollback, config, restart, health and partial-placement failure for every supported combination.

## Phase 2: Distributed Reliability and CI

### Task 2.1: Redis-backed coordination

Files: `backend/app/core/ratelimit.py`, `backend/app/core/ws_hub.py`, new Redis coordination module, worker/API wiring, tests.

Why: process-local state breaks multi-replica rate limiting and websocket fan-out.

Change: preserve existing interfaces while adding Redis implementations, stream/topic namespacing, bounded queues and reconnect behavior; select implementation by deployment profile.

Verification: two-process integration tests prove shared limits and cross-instance event delivery; local in-memory mode remains test/dev-only.

### Task 2.2: Singleton beat lease and CI reconciliation factory

Files: `backend/app/workers/deploy_tasks.py`, `backend/app/workers/celery_app.py`, `backend/app/services/pipeline_provider.py`, config/tests.

Why: the current worker resolver is never wired, so lost deployment webhooks are not reconciled in production.

Change: construct the same serializable provider factory in API and worker from settings/secret store; add Redis lease for singleton beat reconciliation; expose skipped/failed metrics and audit events.

Verification: worker integration tests prove configured provider reconciliation, lease exclusion, restart recovery and explicit skipped status when unconfigured.

### Task 2.3: Build node registration and scheduling

Files: build node API/repository/service, `BuildService`, new scheduler/lease module, frontend build-node controls, migrations/tests.

Why: the current build path always uses the local node.

Change: register SSH build nodes with labels and concurrency, lease capacity atomically, select a compatible node, heartbeat/offline handling, and return a typed no-capacity failure.

Verification: scheduler tests cover capability matching, max concurrency, lease expiry, node failure and local-node compatibility; real disposable SSH builder smoke executes clone/test/build/artifact.

## Phase 3: Advanced Release Providers

### Task 3.1: Kubernetes Argo Rollouts provider

Files: new `backend/app/adapters/argo_rollouts.py`, provider wiring, release strategy, config schemas, tests, Kubernetes manifests/docs.

Why: Kubernetes canary/blue-green currently return 501 without a provider.

Change: implement status, promote, abort and rollback operations with health gate polling and typed provider errors; preserve existing rolling/recreate behavior.

Verification: fake API contract tests plus disposable Kubernetes/Argo smoke for canary, blue-green, abort and rollback.

### Task 3.2: Bare-metal/Docker LoadBalancer provider

Files: new provider interface/implementations, runtime strategy wiring, config schemas, tests/docs.

Why: systemd/Docker canary/blue-green need actual weight/upstream control.

Change: implement a provider contract for supported Nginx/HAProxy/cloud LB APIs, health-gated weight steps, upstream switching and rollback; no generic shell fallback.

Verification: provider contract tests, idempotent switch/rollback tests and disposable LB smoke.

## Phase 4: Deployment Profiles and Recovery

### Task 4.1: Hardened Compose production profile

Files: `docker-compose.yml`, new production compose override, Dockerfiles, healthchecks, docs.

Change: external DB/Redis option, no default secrets, resource limits, worker concurrency, Prometheus retention, TLS reverse proxy boundary and backup command.

Verification: `docker compose config`, image builds, disposable full-stack startup and health smoke.

### Task 4.2: VM/systemd packaging

Files: new `ops/systemd/*.service`, reverse-proxy config, `ops/upgrade.sh`, docs.

Change: API/worker/beat units, secret environment file contract, restart policies, health checks, migration/backup/restore/rollback flow.

Verification: disposable VM or containerized systemd smoke plus upgrade failure rollback.

### Task 4.3: Kubernetes packaging

Files: new `ops/kubernetes/` manifests or Helm chart, ingress/TLS/PDB/HPA/NetworkPolicy, docs.

Change: API/worker replicas, singleton beat lease, external DB/Redis, probes, migrations Job, frontend ingress and observability integration.

Verification: `kubectl`/Helm template validation, disposable cluster deployment and rolling restart smoke.

### Task 4.4: Backup, restore and observability drill

Files: `ops/backup/`, `ops/upgrade.sh`, monitoring configs, docs, runbook tests.

Change: executable PostgreSQL backup/restore, migration rollback procedure, retention policies, dashboards, alerts and audit/log retention checks.

Verification: restore into a disposable database, compare core records, simulate failed upgrade, verify alert delivery and recovery objectives.

## Phase 5: Final Gate

Run backend/frontend full suites, Ruff/Black/ESLint/Prettier/typecheck/build, fresh migration upgrade, package validations and all available real-environment smoke. Any unavailable external dependency remains `blocked` or `needs-verification`, never green by fake.

## Risks and External Dependencies

- Requires an OIDC provider, CA, CI provider, registry, Argo Rollouts, supported LoadBalancer and disposable systemd/Docker/Kubernetes/Agent targets for full acceptance.
- Database schema changes require forward/backward migration review and no live data deletion.
- Existing API and task behavior are shared by every new provider; producer/consumer contract tests are mandatory.
- Full repo format cleanup is a separate mechanical task unless changed files are touched; Phase 5 cannot claim complete while the configured CI gate is red.

## Repair Track

Repair production gaps in the canonical owner: config validation in settings/startup, identity in auth, distributed state in Redis coordination, CI reconciliation in worker provider wiring, runtime actions in adapters/providers, and packaging in `ops/`.

## Retirement Track

Retire production acceptance of insecure defaults, plaintext Agent transport, process-local-only coordination, unconnected worker provider hooks, local-only build assumptions and stale “not implemented” documentation. Dev/test fallbacks remain only when explicitly profile-gated.


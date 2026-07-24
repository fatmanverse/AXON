# 完整平台生产化 - Checkpoint

- Task ID: 2026-07-23-production-platform-readiness
- Current todo: design production boundary and phased acceptance
- Active slice: baseline and production-readiness design
- Blocked on: none
- Next step: present design options and ask for approval before source edits

## Checkpoint Update

- Current todo: obtain written-spec review approval
- Active slice: written design review gate
- Completed todos:
- production scope selected: full platform
- baseline readback and alignment complete
- full platform production-readiness design written and self-reviewed
- Evidence refs:
- docs/aegis/specs/2026-07-23-full-platform-production-readiness-design.md
- docs/aegis/baseline/2026-07-20-initial-baseline.md
- Blocked on: user review of written design spec
- Next step: after approval, invoke writing-plans and create phased implementation plan

## DriftCheckDraft

- Scope status: inside Phase 0 Task 0.1 only
- Compatibility status: dev/staging defaults preserved; prod fail-fast is opt-in by env
- Retirement status: silent production defaults retired; no fallback added
- New risk signals:
- LocalSecretStore remains dev/staging only; production requires Vault
- Advisory decision: continue

## Checkpoint Update

- Current todo: complete final verification and external smoke gate inventory
- Active slice: Phase 5 full suite, package validation and production runbook review
- Completed todos:
- Phase 4 hardened Compose production profile with external DB/Redis
- Phase 4 VM/systemd units and Kubernetes HA manifests with non-root containers, probes, PDB/HPA, migration Job and RBAC
- Backup/restore scripts with checksum and explicit destructive confirmation
- Build node capability scheduling, Redis slot lease, heartbeat and SSH generic artifact return
- Evidence refs:
- ops/compose/docker-compose.prod.yml
- ops/systemd/
- ops/kubernetes/axon.yaml
- ops/backup/
- backend/app/services/build_node_scheduler.py
- Blocked on:
- Kubernetes API schema validation, Docker image builds, real Redis/PostgreSQL/Celery/Argo/LB/SSH/Agent smoke require external runtime access
- OIDC/MFA enterprise identity provider credentials and UX remain external integration gates; local account lifecycle is implemented
- Next step: run full backend/frontend verification, regenerate contracts if needed, inspect diff, then report remaining external gates without claiming production green

## DriftCheckDraft

- Scope status: packaging and scheduler changes remain within ops/build owners; no live data mutated
- Compatibility status: local build/runtime profiles remain available; production profiles are explicit and fail-fast
- Retirement status: local-only build assumption and stale Agent/artifact/provider documentation retired
- Advisory decision: needs-verification

## Checkpoint Update

- Current todo: implement production deployment profiles and recovery drills
- Active slice: Phase 4 Compose/VM/systemd/Kubernetes packaging
- Completed todos:
- Phase 3 Argo Rollouts provider for Kubernetes canary/blue-green
- Phase 3 explicit HTTP LoadBalancer provider for bare-metal/Docker canary/blue-green
- Release strategy and rollback paths now invoke configured rollout providers; missing providers remain typed 501
- Evidence refs:
- backend/app/adapters/argo_rollouts.py
- backend/app/adapters/http_load_balancer.py
- backend/app/services/rollout_provider.py
- backend/tests/unit/test_argo_rollouts.py
- backend/tests/unit/test_http_load_balancer.py
- Blocked on:
- Argo CRD, real LB adapter and disposable runtime smoke require external infrastructure
- Next step: inspect Compose/ops packaging and add hardened production profiles plus VM/Kubernetes manifests

## DriftCheckDraft

- Scope status: provider work stayed behind release_strategy/rollout_provider adapter boundaries
- Compatibility status: existing rolling/recreate behavior and typed unsupported errors remain intact
- Retirement status: hard-coded canary/blue-green 501 is retired when configured providers are available
- Advisory decision: continue

## Checkpoint Update

- Current todo: implement Phase 3 advanced release providers
- Active slice: Phase 3 canary/blue-green provider contracts
- Completed todos:
- Phase 2.1 Redis-backed rate limiting and WebSocket pub/sub fan-out
- Phase 2.2 CI worker provider factory, explicit unconfigured skip, and Redis singleton beat lease
- Evidence refs:
- backend/app/core/ratelimit.py
- backend/app/core/ws_hub.py
- backend/app/core/redis_lease.py
- backend/app/workers/celery_app.py
- backend/app/workers/deploy_tasks.py
- Blocked on:
- Redis multi-process and Celery fork behavior require disposable Redis/worker smoke outside the sandbox
- Next step: implement Argo Rollouts and bare-metal/Docker LoadBalancer providers with typed capability errors

## DriftCheckDraft

- Scope status: distributed coordination and CI reconciliation stayed in Redis/worker owners; API and state-machine contracts unchanged
- Compatibility status: in-memory limiter/Hub remain available for dev/test; production config requires Redis coordination
- Retirement status: process-local-only production coordination and unconnected CI resolver retired
- Advisory decision: continue

## Checkpoint Update

- Current todo: implement Task 1.2 runtime matrix contract tests
- Active slice: Phase 1 runtime support contract and explicit capability errors
- Completed todos:
- Task 1.1 Agent artifact/config/lifecycle execution implemented
- Agent artifact transfer now supports bounded chunks, per-chunk and full-file SHA-256, atomic commit and abort cleanup
- Agent config writes are atomic and constrained to configured roots; install scripts pass explicit TLS/insecure and runtime limits
- Evidence refs:
- backend/app/adapters/agent_gateway.py
- backend/app/adapters/artifact_transfer.py
- backend/app/services/agent_delivery_service.py
- agent/main.go
- agent/main_test.go
- Blocked on:
- Go dependency/toolchain download and real Agent socket smoke require CI/staging network and host permissions
- Next step: add shared runtime matrix contract tests and close unsupported combinations explicitly

## DriftCheckDraft

- Scope status: Task 1.1 stayed within Agent transport, artifact transfer, config delivery and existing runtime owners
- Compatibility status: SSH SFTP remains unchanged; Agent artifact path is additive; protobuf and task/deployment contracts unchanged
- Retirement status: Agent artifact 501 placeholder retired when registry is configured; explicit 501 remains for missing Agent runtime wiring
- Advisory decision: continue

## Checkpoint Update

- Current todo: implement Task 0.2 identity lifecycle and OIDC boundary
- Active slice: Phase 0 security baseline: identity lifecycle
- Completed todos:
- Task 0.1 production configuration fail-fast implemented and verified
- Evidence refs:
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-phase0-config-tests.json
- Blocked on: OIDC provider details are external; local break-glass contract can be implemented first
- Next step: inspect current user model/token flow and add revocation/lockout tests

## Checkpoint Update

- Current todo: implement Task 0.3 Agent mTLS and identity
- Active slice: Phase 0 security baseline: Agent transport security
- Completed todos:
- Task 0.2 identity lifecycle implemented and verified
- Evidence refs:
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-phase0-identity-green.json
- Blocked on: CA and certificate material are external; local TLS protocol smoke can be built without production CA
- Next step: add TLS/mTLS configuration and certificate validation tests for Python gRPC server and Go Agent

## Checkpoint Update

- Current todo: implement Task 1.1 unified Agent artifact/config/lifecycle execution
- Active slice: Phase 1 Agent artifact transfer and atomic delivery
- Completed todos:
- Task 0.3 production Agent mTLS transport, certificate identity binding and emergency revocation implemented
- Evidence refs:
- backend/tests/unit/test_config.py
- backend/tests/unit/test_agent_grpc_server.py
- backend/tests/integration/test_agent_grpc_servicer.py
- backend/tests/integration/test_agent_grpc_e2e.py
- Blocked on:
- localhost gRPC socket binding is prohibited in the current sandbox; real mTLS wire tests remain an external CI/staging gate
- Go 1.25 toolchain is not locally available and network download is blocked; `agent/main_test.go` remains an external CI gate
- Next step: add bounded, checksummed Agent artifact transfer and remove the Agent artifact 501 branch

## DriftCheckDraft

- Scope status: Task 0.3 stayed within Agent transport security and identity
- Compatibility status: SSH path and explicit dev/test insecure mode remain available; stream/task/fence protocol fields are unchanged
- Retirement status: plaintext production Agent transport and unbound certificate identity are retired
- Advisory decision: continue

## Checkpoint Update

- Current todo: 完成生产准入证据与外部环境门禁清单
- Active slice: Phase 5 final verification and external gate accounting
- Completed todos:
- OpenAPI/schema 再生与前端 BuildsPage 制品部署入口
- Redis Agent owner 跨 API 副本路由与 stale heartbeat fencing
- Kubernetes RWX PVC、mTLS Secret、in-cluster client 与前端 Service DNS
- Compose Agent mTLS overlay 与 systemd Nginx TLS 入口
- 后端/前端全量测试、静态检查、构建、迁移往返
- Evidence refs:
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-final-local.json
- Blocked on: 真实 PostgreSQL/Redis/Celery/Go 1.25/Kubernetes/Argo/LB/Agent/OIDC provider 尚未在当前沙箱执行
- Next step: 提交代码并在 CI/预发执行真实 smoke；通过后再生产发布

## DriftCheckDraft

- Scope status: 实现仍在 backend/frontend/agent/ops/docs 既有 owner 边界内；未修改 live data
- Compatibility status: CI、artifact deploy、rollback、approval、SSH、Alembic revision IDs 保持兼容；Agent 多副本新增 Redis owner 路由
- Retirement status: 进程内 Agent owner 不再承担生产跨副本权威；旧 insecure/default 路径仅保留显式 dev/test
- New risk signals:
- 真实 Redis/PostgreSQL/Kubernetes/Argo/LB/Agent/OIDC smoke 与 Go 1.25 尚未执行
- Advisory decision: needs-verification

## Checkpoint Update

- Current todo: 完成生产准入证据与外部环境门禁清单
- Active slice: Phase 5 final verification and external gate accounting
- Completed todos:
- OpenAPI/schema 再生与前端 BuildsPage 制品部署入口
- Redis Agent owner 跨 API 副本路由与 stale heartbeat fencing
- Kubernetes/Compose/systemd 生产 packaging
- 后端/前端全量测试、静态检查、构建、迁移往返
- Evidence refs:
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-final-backend-frontend.json
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-final-contract-migrations.json
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-final-packaging.json
- docs/aegis/work/2026-07-23-production-platform-readiness/evidence-bundle-draft-final-agent-routing.json
- Blocked on: 真实 PostgreSQL/Redis/Celery/Go 1.25/Kubernetes/Argo/LB/Agent/OIDC provider 尚未在当前沙箱执行
- Next step: 提交代码并在 CI/预发执行真实 smoke；通过后再生产发布

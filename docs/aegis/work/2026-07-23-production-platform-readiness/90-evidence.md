# 完整平台生产化 - Evidence

No evidence has been recorded yet.

## EvidenceBundleDraft

- Artifact key: phase0-config-tests
- Type: command
- Source: backend/tests/unit/test_config.py
- Summary: 生产配置 fail-fast RED/GREEN: default JWT, local secret backend/default seed rejection and valid prod config pass
- Verifier: uv run pytest tests/unit/test_config.py -q: 5 passed

## EvidenceBundleDraft

- Artifact key: phase0-config-green
- Type: command
- Source: backend/app/core/config.py, backend/app/main.py, backend/app/cli/seed.py, backend/alembic/env.py
- Summary: Production validation is wired into API, seed and Alembic entrypoints; docs and env contract updated
- Verifier: targeted Ruff/Black passed; 14 backend config/auth/export/health tests passed

## EvidenceBundleDraft

- Artifact key: phase0-identity-green
- Type: command
- Source: backend/app/api/auth.py, backend/app/services/auth_service.py, backend/app/core/security.py, backend/app/models/user.py, backend/alembic/versions/user_auth_lifecycle.py
- Summary: Identity lifecycle: failed-login lockout, password change, token-version revocation, logout, app-state Settings owner and migration
- Verifier: auth integration: 8 passed; targeted Ruff/Black passed; fresh SQLite upgrade and downgrade passed; make gen-api passed

## EvidenceBundleDraft

- Artifact key: phase0-agent-mtls-green
- Type: command
- Source: backend/app/core/config.py, backend/app/services/agent_grpc.py, backend/app/services/agent_grpc_server.py, agent/main.go
- Summary: Production Agent transport requires mTLS, client certificate SAN/CN is bound to agent_id, emergency identity revocation is supported, and Go Agent requires TLS unless --insecure is explicit
- Verifier: 16 Python config/server/servicer tests passed; targeted Ruff and Black passed

## EvidenceBundleDraft

- Artifact key: phase0-agent-mtls-external-gates
- Type: command
- Source: backend/tests/integration/test_agent_grpc_e2e.py, agent/main_test.go
- Summary: Real localhost mTLS acceptance and identity mismatch tests are present; Go TLS option tests are present
- Verifier: current sandbox blocked gRPC bind with Operation not permitted; Go 1.25 download blocked by restricted network, so both remain CI/staging gates

## EvidenceBundleDraft

- Artifact key: phase1-agent-artifact-green
- Type: command
- Source: backend/app/adapters/agent_gateway.py, backend/app/adapters/artifact_transfer.py, backend/app/services/executor_factory.py, backend/app/services/agent_delivery_service.py, agent/main.go
- Summary: Agent artifact direct deployment path supports bounded checksummed chunks, atomic target commit, abort cleanup, path traversal/symlink protection, atomic config writes, and explicit install transport arguments
- Verifier: 54 targeted backend tests passed; targeted Ruff and Black passed; Go source formatted and Go tests added but dependency/toolchain unavailable in sandbox

## EvidenceBundleDraft

- Artifact key: phase2-distributed-coordination-green
- Type: command
- Source: backend/app/core/ratelimit.py, backend/app/core/ws_hub.py, backend/app/core/redis_lease.py, backend/app/main.py, backend/app/workers/celery_app.py, backend/app/workers/deploy_tasks.py
- Summary: Redis atomic rate limiting, cross-instance WebSocket fan-out, worker-safe publishing, singleton deployment reconciliation lease, and CI provider factory wiring are implemented with explicit unavailable/skip semantics
- Verifier: 23 rate-limit/middleware tests, 13 Hub/realtime/Celery tests, and 10 lease/reconciliation tests passed; targeted Ruff/Black passed

## EvidenceBundleDraft

- Artifact key: phase3-release-providers-green
- Type: command
- Source: backend/app/adapters/argo_rollouts.py, backend/app/adapters/http_load_balancer.py, backend/app/services/release_strategy.py, backend/app/services/rollout_provider.py, backend/app/services/deployment_service.py
- Summary: Argo Rollouts promote/abort/health wait and HTTP LB weight/switch providers are wired into canary/blue-green strategy and CI rollback paths with explicit errors
- Verifier: 34 release/k8s/rollback tests passed; targeted Ruff/Black passed

## EvidenceBundleDraft

- Artifact key: phase4-packaging-build-green
- Type: command
- Source: ops/compose/docker-compose.prod.yml, ops/systemd, ops/kubernetes/axon.yaml, ops/backup, backend/app/services/build_node_scheduler.py, backend/app/services/build_service.py
- Summary: production Compose/VM/Kubernetes entrypoints, backup/restore scripts, non-root images and SSH build node scheduling are present
- Verifier: shell syntax and git diff checks passed; Compose config parsed with placeholder images; Kubernetes YAML parsed into 16 documents; build/scheduler targeted suite 25 passed; kubectl API validation blocked by sandbox permissions

## EvidenceBundleDraft

- Artifact key: final-backend-frontend
- Type: command
- Source: backend/tests,frontend/tests,Makefile
- Summary: 后端 648 passed/4 socket-boundary skipped, coverage 84.42%;前端 70 passed; Ruff/Black/ESLint/Prettier/TypeScript build passed
- Verifier: make backend-test; make backend-lint; cd frontend && npm test && npm run lint && npm run format:check && npm run build

## EvidenceBundleDraft

- Artifact key: final-contract-migrations
- Type: command
- Source: backend/openapi.json,frontend/src/api/schema.d.ts,backend/alembic/versions
- Summary: make gen-api 成功；schema 只保留真实契约差异；SQLite Alembic upgrade/downgrade/upgrade 往返成功，head=e5f6a7b8c9d0
- Verifier: make gen-api; alembic upgrade head; alembic downgrade base; alembic upgrade head

## EvidenceBundleDraft

- Artifact key: final-packaging
- Type: command
- Source: ops/compose,ops/kubernetes,ops/systemd,ops/backup
- Summary: Compose 基础与 Agent overlay config 解析成功；Kubernetes 18 文档解析成功；shell 脚本语法通过；RWX PVC、mTLS Secret、in-cluster client、Service DNS 已声明
- Verifier: docker compose ... config --quiet; python yaml.safe_load_all; bash -n ops/*.sh

## EvidenceBundleDraft

- Artifact key: final-agent-routing
- Type: command
- Source: backend/app/services/redis_agent_connection.py,backend/tests/unit/test_redis_agent_connection.py
- Summary: 31 条 Agent/gateway/gRPC/连接回归通过；Redis owner、跨副本命令/结果、stale heartbeat fencing、typed 503 已验证
- Verifier: uv run pytest tests/unit/test_redis_agent_connection.py tests/unit/test_agent_gateway.py tests/integration/test_agent_grpc_servicer.py tests/integration/test_agent_connection.py tests/integration/test_agent_gateway_real.py -q

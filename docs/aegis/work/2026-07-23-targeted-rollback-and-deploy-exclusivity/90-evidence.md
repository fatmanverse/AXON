# Evidence Bundle Draft

## Behavior

- Task repository / partial index: `10 passed` targeted tests。
- Deployment operation API producers: `30 passed` targeted tests。
- Rollback target repository/API/approval: `35 passed` targeted tests。
- CI/artifact rollback, approval, health-fail and alert regression: `31 passed` targeted tests。
- Deployment-related regression: `48 passed`。
- DeploymentsPage: `7 passed` integration tests。

## Full Verification

- Backend: `608 passed` with `tests/integration/test_agent_grpc_e2e.py` excluded only for sandbox port binding。
- gRPC E2E: `2 failed` because sandbox returned `Operation not permitted` while binding `0.0.0.0:0`; no assertion or application-logic failure reached。
- Frontend: `70 passed` across 14 files；ESLint passed；TypeScript/Vite build passed。
- Ruff: `app tests` plus new migration passed。
- Black: 18 changed Python files passed；repository-wide check still reports 41 pre-existing formatting-debt files。
- Migration: fresh SQLite upgraded from base through `d4e5f6a7b8c9` successfully。
- Contract: `make gen-api` succeeded；OpenAPI and TS schema contain optional `RollbackRequestBody.target_deployment_id`。
- Diff: `git diff --check` passed。

## Covered Scope

- Explicit/implicit target validation and error contracts。
- Artifact/CI rollback dispatch and success/failure state closure。
- Same-service DB-backed deployment operation exclusivity and approval retry semantics。
- Deployment-history artifact display, detail, row confirmation, approval and task terminal handling。
- Generated contracts, usage docs, ADR and baseline sync。

## Uncovered Scope / Residual Risk

- gRPC E2E needs a host allowed to bind local ports。
- PostgreSQL migration SQL was not executed against a live PostgreSQL instance; SQLAlchemy metadata, Alembic generation and fresh SQLite upgrade were verified。
- Existing active task duplicates would make the production migration fail explicitly; migration intentionally does not delete audit/task data。
- A stale pending/running deployment task can intentionally block new operations until investigated。

# Artifact Direct Deployment — Evidence

## Task 2 — SSH/SFTP Artifact Transfer

- Commits: `fd7f9a7`, `f080a2f`, `f9043ef`, `44176c7`。
- RED: AsyncSSH 2.24 raised `TypeError: unexpected keyword argument 'client_key'`; SSH executor `client_keys` assertion failed; symlink upload did not raise。
- GREEN: `PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m pytest backend/tests/unit/test_artifact_transfer.py backend/tests/unit/test_ssh_executor.py backend/tests/unit/test_executor.py -q` → 32 passed。
- Static: Ruff passed；Black check passed；`git diff --check` passed。
- Retirement: SSH executor and artifact transfer now share `build_ssh_connect_kwargs`; no legacy `client_key` remains in the SSH adapter path；symlink artifacts are rejected before connection。

## Tasks 3-7 — Runtime Owner Through UX and Contract

- Runtime owner: `ArtifactDeploymentService` target + runtime regression → 61 passed during Task 3 review。
- State orchestration: deployment/artifact/health/scan regression → 34 passed during Task 4 review。
- API/governance: API、approval、quality gate、notification、service regression → 52 passed during Task 5 review。
- Frontend: BuildsPage target 5 passed；full frontend 66 passed；lint/build passed。
- Final backend target: 60 passed；Ruff all app/tests passed；Black key files passed。
- Final backend regression: unit 237 passed；integration group A 183 passed；integration group B 176 passed；gRPC E2E excluded for sandbox listener restriction。
- Contract: OpenAPI/TypeScript generated diff limited to `artifact_id` and optional `version` request contract；DeploymentOut field already existed in generated baseline。
- Uncovered: Go Agent tests blocked before execution because sandbox denied toolchain cache write；real SSH/SFTP、Docker daemon and Kubernetes cluster smoke remain operational checks。

## Baseline

- Isolated branch created from `0461445`。
- Dependency directories linked from the verified main workspace for local execution only；links are ignored and will not be committed。
- `backend/.venv/bin/pytest tests --ignore=tests/integration/test_agent_grpc_e2e.py -q` → 556 passed，232 warnings，exit 0。
- `frontend npm run lint && npm run test && npm run build` → 63 tests passed，lint/build exit 0；仅既有 AntD/Router/bundle-size warnings。
- Worktree status restored clean after removing generated `tsconfig.tsbuildinfo` side effect。

## Task Evidence

- Task 1 commit: `a237df7aedffbdef5292c2912efc9732e80bd45b`。
- Task 1 RED: 5 expected failures。
- Task 1 GREEN: 23 target tests passed；Ruff/Black/diff check passed。
- Task 1 compatibility: old CI deploy/approval regression 17 passed。
- Task 1 spec review: compliant。
- Task 1 quality review: no Critical/Important/Minor findings；advisory ready yes。
- Task 2: pending。
- Task 3: pending。
- Task 4: pending。
- Task 5: pending。
- Task 6: pending。
- Task 7: pending。

# Proof Bundle - 2026-07-20-artifact-direct-deployment

## Method Pack Boundary

This proof bundle is an advisory Aegis Method Pack record. It does not determine evidence sufficiency, produce authoritative `GateDecision`, or grant `completion authority`.

## Task Intent

- Requested outcome: 从构建页选择明确 artifact，并真实部署到所属 service 的 runtime。
- Scope: backend artifact contract, transfer, runtime execution, deployment orchestration, API governance, frontend entry, generated contract and operations docs

## Impact

- Compatibility boundary: Requests without artifact_id continue through CI; existing approval, quality gate, task and deployment contracts remain compatible.
- Non-goals:
- cross-service or cross-environment direct deploy
- automatic latest artifact selection
- Agent systemd upload
- advanced direct strategies
- router decomposition

## Evidence Bundle Refs

- docs/aegis/work/2026-07-20-artifact-direct-deployment/evidence-bundle-draft.json

## Drift Check

- Scope status: verified
- Compatibility status: verified
- Retirement status: old client_key path deleted; generic Executor.deploy retained only as documented compatibility
- Advisory decision: needs-verification

# ADR-0001 - Targeted Rollback and Deployment Exclusivity

Status: `recorded-from-work`
Date: `2026-07-23`

## Source Evidence

- Approved design, implementation plan, work checkpoint, migrations, generated contracts, and full test evidence.
## Context

Rollback previously redeployed the current version and deployment task creation had no race-safe same-service exclusion. Artifact history also required its canonical runtime deployment owner.

## Decision

Rollback resolves a concrete historical deployment snapshot; artifact targets use ArtifactDeploymentService and CI targets use PipelineAdapter. TaskRepository is the sole deploy/rollback task creator and a partial unique index on active task targets enforces same-service exclusivity.

## Alternatives Considered

- API query-before-create was rejected because it races.
- A separate lock table or deployment queue was rejected as out of scope.
- Caller-side artifact URI fallback was rejected because it duplicates the runtime owner.
## Consequences

- Concurrent deploy, rollback, promotion, or approval execution returns deployment_in_progress instead of queueing.
- Pending approvals do not hold the deployment slot; approval conflicts remain pending.
- A stale pending/running task can block later operations and must be investigated explicitly.
## Compatibility Boundary

Rollback without a body follows current.previous_deployment_id; CI deployment, webhook, lifecycle, build, and config task contracts remain compatible.

## Retirement Impact

Retires current-as-target rollback, top-level one-click rollback UI, and direct generic task creation for deployment operations.

## Baseline Sync

- Needed: needed
- Target: docs/aegis/baseline/2026-07-20-initial-baseline.md
- Action: update baseline
- Reason: Canonical task creation owner, rollback contract, and runtime ownership are now current architecture facts.

## Evidence References

- docs/aegis/work/2026-07-23-targeted-rollback-and-deploy-exclusivity/
- docs/aegis/plans/2026-07-23-targeted-rollback-and-deploy-exclusivity.md
- backend/tests/integration/test_rollback_service.py
- backend/tests/integration/test_deployment_task_exclusivity.py
- frontend/tests/integration/DeploymentsPage.test.tsx
## Boundary

This ADR is an advisory Aegis Method Pack record. It does not grant completion authority or replace project-authoritative architecture sources.

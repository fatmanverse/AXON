# ADR-0001 - Artifact Direct Deployment Owner and Compatibility Boundary

Status: `recorded-from-work`
Date: `2026-07-22`

## Source Evidence

- Approved design/plan, Tasks 1-7 commits, backend and frontend regression evidence
## Context

Artifacts were registered but no canonical production owner connected artifact identity, transfer, runtime execution, governance, and deployment state. Expanding rollout_provider or API branching would duplicate ownership.

## Decision

ArtifactDeploymentService owns artifact-to-runtime validation and execution; ArtifactTransfer is a narrow SSH/SFTP boundary; DeploymentService retains task/deployment state; the existing endpoint branches explicitly between CI and artifact modes.

## Alternatives Considered

- Expand rollout_provider to own artifact transfer and deployment; rejected because release strategy does not own artifact identity.
- Implement runtime branching in the API; rejected because the API must remain transport and governance only.
- Expand Executor.deploy for all transports; rejected because generic upload is not a universal executor capability.
## Consequences

- generic artifacts deploy only to systemd over SSH/SFTP; docker artifacts deploy to Docker or Kubernetes; direct mode is rolling-only and can partially update multiple placements before failure.
## Compatibility Boundary

Requests without artifact_id continue through CI unchanged; direct mode preserves permission, quality gate, approval, audit, task, deployment, rollback, and promotion contracts.

## Retirement Impact

Duplicate SSH authentication branches and the invalid client_key option were deleted. Executor.deploy remains only for interface compatibility and is not the direct-deployment owner.

## Baseline Sync

- Needed: needed
- Target: docs/aegis/baseline/2026-07-22-artifact-direct-deployment-baseline.md
- Action: create snapshot
- Reason: The initial baseline states artifacts are not runtime-deployable; a new snapshot must record the landed owners and compatibility boundary without rewriting history.

## Evidence References

- docs/aegis/specs/2026-07-20-artifact-direct-deployment-design.md
- docs/aegis/plans/2026-07-20-artifact-direct-deployment.md
- docs/aegis/work/2026-07-20-artifact-direct-deployment/90-evidence.md
## Boundary

This ADR is an advisory Aegis Method Pack record. It does not grant completion authority or replace project-authoritative architecture sources.

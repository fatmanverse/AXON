# Artifact Direct Deployment — Checkpoint

## TodoCheckpointDraft

- Current todo: Task 2 — Add Narrow SSH/SFTP Artifact Transfer。
- Completed todos: setup；Task 1 implementation + spec review + quality review。
- Active slice: ArtifactTransfer protocol、SSH/SFTP implementation、executor factory target reuse。
- Evidence refs: baseline；Task 1 commit `a237df7`；23 target tests + 17 CI regression passed；both reviews approved。
- Blocked on: none。
- Next step: dispatch fresh Task 2 implementer with transfer-only boundary。

## ResumeStateHint

- Branch: `feature/artifact-direct-deployment`。
- Workspace: `.worktrees/artifact-direct-deployment/` isolated clone fallback。
- Main workspace remains untouched by business implementation。
- Re-read intent、plan、latest checkpoint before resuming。

## DriftCheckDraft

- Original intent served: yes；Task 1 established approved artifact contract。
- Compatibility boundary held: yes；old CI schema remains valid。
- New owner/fallback introduced: no；repository methods stay canonical。
- Retirement track explicit: yes，plan retains Executor.deploy review trigger。
- Decision: `continue`。

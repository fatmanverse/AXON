# Todo Checkpoint

- Current todo: handoff complete working tree。
- Completed todos: approved design、implementation plan、自审、Tasks 1-6 implementation/contracts/docs/verification。
- Active slice: none；all planned tasks completed。
- Evidence refs: `90-evidence.md`；ADR-0001；baseline sync；full backend/frontend/static/build/migration results。
- Blocked on: 无。
- Next step: user chooses whether to keep, commit, or integrate the current main working tree。

## ResumeStateHint

- 工作区已有 Task 6/7 未提交改动，必须保留。
- 不启动子代理；用户选择 inline execution。
- 所有 shell 命令使用 `rtk`；uv cache 使用 `/tmp/axon-uv-cache`。

## DriftCheckDraft

- Scope: 对齐批准设计。
- Compatibility: 已在计划固定。
- New owner/fallback: 仅新增有证明的 repository helper/index，无 fallback。
- Retirement: 所有 caller 迁移和旧顶部回滚入口删除已列入计划。
- Decision: `continue`；fresh full verification, diff review, ADR/baseline sync and workspace checks completed。

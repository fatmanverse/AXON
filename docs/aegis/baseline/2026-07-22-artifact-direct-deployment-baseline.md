# Axon Artifact Direct Deployment Baseline

Date: `2026-07-22`
Status: `verified current-state snapshot`
ADR: `docs/aegis/adr/ADR-0001-artifact-direct-deployment-owner.md`

## Product / Requirement Baseline

- 运维人员从构建页选择明确 artifact，并部署到其所属 service。
- `generic` artifact 只支持 `systemd`；`docker` artifact 支持 Docker 和 Kubernetes。
- artifact 直发首版仅支持 `rolling`，不自动选择最新 artifact，不允许跨 service 直发。
- 直发继续受权限、质量门禁、生产审批、四眼原则、审计、task 和 deployment 状态约束。
- 不带 `artifact_id` 的部署请求继续走原 CI 路径。

## Architecture / Runtime Boundary Baseline

- `ArtifactDeploymentService` 是 artifact 身份、类型/runtime 兼容、runtime_ref 解析、placement 与直接执行的 canonical owner。
- `ArtifactTransfer` 是本地 generic artifact 上传的窄接口；当前生产实现仅支持 SSH/SFTP。
- `DeploymentService` 继续拥有 task/deployment 状态编排，不拥有 SSH、SFTP 或 runtime 命令翻译。
- `SystemdRuntime`、`DockerRuntime`、`K8sRuntime` 继续拥有各自部署动作翻译。
- API 只负责 transport、权限、门禁、审批、metadata 一致性和依赖装配；`BuildsPage` 提供唯一 artifact 行入口。

## Compatibility and Failure Boundary

- artifact URI、version、git SHA 和 registry type 以 artifact/registry 记录为准。
- systemd 在远端动作前验证 `unit_name`、`deploy_path`、placement 和 SSH 上传能力；上传路径固定为 `/tmp/axon-artifacts/<artifact_id>.tar.gz`。
- Docker placement 按稳定顺序串行，首个失败停止；已完成目标不伪装回滚，整体 deployment/task 落 failed。
- Kubernetes 只 patch 一次 Deployment image，不重复执行 release strategy。
- systemd 清理失败记录 warning，不覆盖 deploy 主结论。

## Retirement State

- AsyncSSH 认证参数由 `build_ssh_connect_kwargs` 单一维护；旧 `client_key` 与重复认证分支已退役。
- `Executor.deploy()` 保留为既有接口兼容，但不是 artifact 直发主路径；后续确认无生产 consumer 后可单独评估退休。

## Verified Evidence

- Backend targeted: 60 passed。
- Backend regression: 237 unit + 183 integration group A + 176 integration group B passed；受限沙箱排除 gRPC listener E2E。
- Frontend: lint passed；66 tests passed；production build passed。
- Static: Ruff、Black、`git diff --check` passed。
- Go Agent tests 未执行：沙箱禁止写入用户 toolchain cache。

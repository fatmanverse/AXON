# 完整平台生产化 - Reflection

## Goal

补齐 Axon 在 SSH/Agent、systemd/Docker/Kubernetes 目标运行时，以及 Compose、VM/systemd、Kubernetes 控制面部署形态上的生产边界。

## DeeperCause

跨副本 Agent 连接不是粘性会话问题，而是进程内连接 owner 与 HTTP/后台任务副本之间缺少共享路由；Redis owner、命令通道、结果广播和 stale heartbeat fencing 已在 canonical connection owner 中实现。

## Evidence

- 后端全量：648 passed，4 个真实 socket E2E 因当前沙箱禁止 bind 而显式 skipped，覆盖率 84.42%。
- 前端全量：70 passed；ESLint、Prettier、TypeScript build 通过。
- OpenAPI/schema 再生、SQLite migration upgrade/downgrade/upgrade、Compose/Kubernetes/shell 静态验证通过。
- Agent 跨副本路由、旧 owner 心跳 fencing 和 typed 503 回归通过。

## Risk / Unknown

真实 PostgreSQL/Redis/Celery、Go 1.25 Agent、Kubernetes API/RWX PVC、Argo Rollouts、LoadBalancer、mTLS socket 和 OIDC provider 尚未在本环境执行；前端构建仍有约 2.3 MB 主包性能警告。生产准入不能在这些门禁完成前宣称通过。

## Decision

needs-verification。代码与本地验证边界已完成，下一步是 CI/预发真实 smoke、备份恢复和升级回滚演练；方法包记录不授予完成权限。

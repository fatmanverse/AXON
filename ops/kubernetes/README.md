# Kubernetes 生产部署

`axon.yaml` 是可审计的基础 HA 清单，依赖外部 PostgreSQL、Redis、支持 `ReadWriteMany` 的 StorageClass、Ingress TLS secret、`axon-secrets` 和 `axon-agent-tls` Secret。先替换两个镜像地址、`axon.example.com`、`agent-grpc.example.com:50051`，再创建 secret；不要把真实 secret 提交到仓库：

```bash
kubectl -n axon-system create secret generic axon-secrets \
  --from-literal=YIMAI_DATABASE_URL='postgresql+asyncpg://axon:...@db.example.internal:5432/axon' \
  --from-literal=YIMAI_REDIS_URL='rediss://:...@redis.example.internal:6379/0' \
  --from-literal=YIMAI_JWT_SECRET='...' \
  --from-literal=YIMAI_SECRET_BACKEND=vault \
  --from-literal=YIMAI_VAULT_ADDR='https://vault.example.internal:8200' \
  --from-literal=YIMAI_VAULT_TOKEN='...' \
  --from-literal=YIMAI_WEBHOOK_SECRETS='{"ci-prod":"..."}' \
  --from-literal=YIMAI_SEED_ADMIN_USER=axon-admin \
  --from-literal=YIMAI_SEED_ADMIN_PASSWORD='...'
kubectl -n axon-system create secret generic axon-agent-tls \
  --from-file=tls.crt=./server.crt \
  --from-file=tls.key=./server.key \
  --from-file=ca.crt=./client-ca.crt
kubectl apply -f axon.yaml
kubectl -n axon-system rollout status deploy/axon-api
```

`axon-artifacts` 与 `axon-builds` 必须由 RWX 存储提供，让 API 与 worker 副本看到同一制品和构建工作区；不支持 RWX 的集群应先改为外部对象存储，不能把 PVC 降为各 Pod 独立的 RWO 卷。Agent 连接 owner、命令和结果通过 Redis 路由，Agent gRPC 长连接可落到任意 API Pod；Redis 不可用时相关操作明确返回失败，不会伪成功。

清单默认启用 in-cluster Kubernetes client。若使用 Argo Rollouts，把 API ServiceAccount 的 Role 规则扩展到实际 workload namespace，并设置 `YIMAI_ARGO_ROLLOUTS_ENABLED=true`；集群必须先安装 Argo Rollouts CRD。`axon-beat` 保持单副本；补偿任务另有 Redis lease，滚动升级时不应同时运行两个 beat。

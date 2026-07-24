"""应用配置:全部从环境变量加载(前缀 YIMAI_)。"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YIMAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "一脉 Axon 控制面"
    env: Env = "dev"
    debug: bool = False

    # 日志
    log_json: bool = True
    log_level: str = "INFO"

    # CORS 白名单(T0.12 使用)
    cors_origins: list[str] = ["http://localhost:5173"]

    # 数据库:生产走 asyncpg,本地/测试可用 aiosqlite
    database_url: str = "postgresql+asyncpg://yimai:yimai@localhost:5432/yimai"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # 认证(JWT)
    jwt_secret: str = "CHANGE-ME-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 480
    auth_max_failed_attempts: int = 5
    auth_lockout_minutes: int = 15

    # 凭证保险箱(§13):local(Fernet)| vault
    secret_backend: Literal["local", "vault"] = "local"
    secret_master_key: str = ""  # local 后端主密钥(生产由 KMS 注入,不落配置文件)
    vault_addr: str = ""
    vault_token: str = ""

    # 首次 seed 管理员。生产必须通过外部 secret 注入,禁止使用开发默认值。
    seed_admin_user: str = "admin"
    seed_admin_password: str = "admin"

    # 任务队列(Celery + Redis)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False

    # 网关限流(T0.12):开发/测试可用进程内桶,生产必须 Redis 分布式实现
    coordination_backend: Literal["memory", "redis"] = "memory"
    rate_limit_enabled: bool = True
    rate_limit_capacity: int = 120  # 桶容量(突发上限)
    rate_limit_refill_per_sec: float = 20.0  # 每秒补充速率(稳态 QPS)
    rate_limit_retry_after: int = 1  # 429 响应的 Retry-After 秒数
    # 限流豁免路径前缀(T0.12):webhook 走自身 HMAC 鉴权(§8.3),CI/扫描器/Alertmanager
    # 的正常突发上报不应被用户级 IP 限流误伤;命中任一前缀的请求跳过限流。
    rate_limit_exempt_prefixes: list[str] = ["/api/webhooks"]
    # 请求体大小上限(T0.12,字节):超限返回 413,防超大请求体耗尽内存。默认 2MiB。
    max_request_body_bytes: int = 2 * 1024 * 1024

    # 服务状态采集(T1.12):SSH 轮询补齐间隔(秒);Agent 接入后由心跳取代(§6.1)
    status_collect_interval_sec: float = 30.0

    # 部署轮询兜底(T2.7,§8.2/§8.3④):定时补齐仍卡 running 的部署(webhook 丢失时),
    # 与 webhook 靠状态机去重。间隔(秒);0 或负数关闭。
    deploy_reconcile_interval_sec: float = 60.0
    deploy_reconcile_lease_ttl_sec: float = 180.0

    # 监控自举(T1.13):node_exporter 版本/端口 + Prometheus file_sd 目标文件路径
    node_exporter_version: str = "1.8.2"
    node_exporter_port: int = 9100
    prometheus_targets_file: str = "/etc/prometheus/targets/nodes.json"

    # Prometheus 查询代理(T1.14):控制面屏蔽直连,前端只经此端点取指标(§15.4)
    prometheus_base_url: str = "http://prometheus:9090"
    prometheus_query_timeout_sec: float = 10.0
    # PromQL 白名单前缀:只放行主机资源族指标,拦截任意指标探测(§15.4)
    metrics_allowed_prefixes: list[str] = [
        "up",
        "node_cpu_seconds_total",
        "node_memory_",
        "node_filesystem_",
        "node_disk_",
        "node_network_",
        "node_load",
    ]
    metrics_max_query_len: int = 2000

    # 入向 webhook(T2.4,§8.3):每个上报源(CI project / scanner)独立 HMAC secret,
    # 键为源标识(X-Webhook-Source 头),值为该源 secret(轮转期可用逗号分隔双 secret)。
    # 生产由环境注入 YIMAI_WEBHOOK_SECRETS='{"gitlab-prod":"s1,s2"}';MVP 缺省为空,
    # 无匹配源即拒绝。时间窗防重放(秒)。
    webhook_secrets: dict[str, str] = {}
    webhook_timestamp_window_sec: int = 300
    # 部署质量门禁(§7.2):存在 critical 漏洞则拦截部署
    deploy_block_on_critical: bool = True
    # 告警触发自动回滚(§11.2):默认关闭,改变生产状态须显式开启
    auto_rollback_on_alert: bool = False
    # 告警自动回滚防抖窗(秒,§6.3):同一 fingerprint 在窗内已触发过回滚则跳过,
    # 避免同一告警反复 firing 上报导致重复回滚(抖动误触)。0 或负数关闭防抖。
    auto_rollback_debounce_sec: float = 600.0
    # 发布后健康检查失败自动回滚(§11.1/§11.2):默认关闭。开启后部署健康检查未通过时,
    # 除标记 FAILED 外再自动回滚到上一版成功制品(留 rolled_back 记录),而非只标失败。
    auto_rollback_on_health_fail: bool = False
    # 生产审批流(§10.2/§13):开启时 prod 的 deploy/delete/rollback 先落 pending 审批,
    # 具 approve 权限者批准后才执行。默认开启——生产高危操作应有人工闸门。
    require_prod_approval: bool = True
    # 通知触达(§13):IM 自定义机器人 webhook URL(钉钉/飞书/企微/Slack)。
    # 留空则不通知(NoopNotifier)。firing 告警到达时推送;后续可扩展关键操作通知。
    notify_webhook_url: str = ""
    # CI pipeline 生产配置(T2.7,§8.1):按 service.name 构造 Jenkins/GitLab adapter。
    # 键为 service 名(或 "*" 默认),值含 provider/base_url/token 保险箱 id 等。
    # 生产由环境注入 YIMAI_PIPELINE_CONFIG='{"*":{"provider":"gitlab",...}}';
    # 缺省为空 → provider 恒返回 None,部署报"未配置 CI"而非 500。
    pipeline_config: dict[str, dict[str, str]] = {}
    # k8s 生命周期/发布策略(T1.10/T3.6):对 runtime=k8s 的服务经 client 执行动作。
    # 默认关闭——纯裸机部署无需 k8s client。开启后启动时加载一次连接配置(in-cluster
    # 或 kubeconfig 文件),构造共享 AppsV1Api,供生命周期与发布策略铺开使用。
    k8s_enabled: bool = False
    k8s_in_cluster: bool = False  # True:用 Pod 内 ServiceAccount(控制面自身跑在集群里)
    k8s_kubeconfig: str = ""  # 非 in-cluster 时的 kubeconfig 路径;空则用默认查找路径
    k8s_default_replicas: int = 1  # start/scale 回的默认副本数(§14.2 副本不落库)
    argo_rollouts_enabled: bool = False
    argo_rollouts_group: str = "argoproj.io"
    argo_rollouts_version: str = "v1alpha1"
    argo_rollouts_plural: str = "rollouts"
    argo_rollouts_health_timeout_sec: float = 300.0
    load_balancer_config: dict[str, str] = {}

    # Agent gRPC server(T4.1,§15.5):Agent 主动外连的监听地址。enabled 关闭时
    # 不起 gRPC server(纯 SSH 部署,默认);开启后 Agent 可建双向流上报/收命令。
    agent_grpc_enabled: bool = False
    agent_grpc_host: str = "0.0.0.0"  # noqa: S104 - Agent 需从各内网机器外连,须监听全网卡
    agent_grpc_port: int = 50051
    # Agent gRPC 传输安全。开发/测试可显式关闭 TLS 使用 insecure；生产启用
    # Agent 时强制双向 TLS，并要求服务端证书、私钥和客户端 CA 文件。
    agent_grpc_tls_enabled: bool = False
    agent_grpc_server_cert_file: str = ""
    agent_grpc_server_key_file: str = ""
    agent_grpc_client_ca_file: str = ""
    # 紧急吊销:证书身份与 agent_id 绑定后，命中列表即拒绝建流。
    agent_grpc_revoked_agent_ids: list[str] = []
    # Agent 自举连接参数。证书路径是目标机上的路径，证书内容应由企业配置
    # 管理/secret volume 预置，不由控制面在安装脚本中回显。
    agent_grpc_server_address: str = ""
    agent_grpc_client_ca_path: str = "/etc/axon/tls/ca.crt"
    agent_grpc_client_cert_path: str = "/etc/axon/tls/agent.crt"
    agent_grpc_client_key_path: str = "/etc/axon/tls/agent.key"
    agent_grpc_client_server_name: str = ""
    agent_insecure_install: bool = False
    agent_config_roots: list[str] = ["/etc/axon"]
    agent_artifact_staging_dir: str = "/tmp/axon-artifacts"
    agent_artifact_chunk_bytes: int = 192 * 1024
    agent_artifact_max_bytes: int = 1024 * 1024 * 1024
    # 心跳超时窗(秒):超过未收到心跳判 agent 离线,触发 §5.4 离线分档与 fencing。
    agent_heartbeat_timeout_sec: float = 30.0

    # 离线分发(需求4):目标机经 SSH 装 node_exporter / axon-agent 时,从控制面下载
    # 端点拉二进制而非公网 github——很多生产内网机器不通外网。dist_dir 是控制面本地
    # 预置二进制的目录;control_plane_base_url 是目标机回连控制面的地址(装机脚本内
    # curl 该地址 + /api/dist/<file>)。留空 base_url 时装机端点仍可用,但安装脚本
    # 需调用方显式传 download_url。
    dist_dir: str = "/var/lib/axon/dist"
    control_plane_base_url: str = ""
    # axon-agent 版本与落地位置(经 SSH 下发,需求4)。二进制名约定
    # axon-agent-{version}-linux-amd64,预置在 dist_dir 下。
    agent_version: str = "0.1.0"
    agent_install_dir: str = "/usr/local/bin"
    agent_service_name: str = "axon-agent"

    # 本地构建(构建能力一期,方案 A)。build_workspace_dir 是控制面主机上每次构建
    # 的 git clone 工作区根目录(用完清理);build_artifacts_dir 是 generic 形态 tar
    # 制品的落点;build_step_timeout_sec 限制单个构建步骤(clone/测试/build)的最长
    # 执行时间,防跑飞构建长期占用节点。
    build_workspace_dir: str = "/var/lib/axon/builds"
    build_artifacts_dir: str = "/var/lib/axon/artifacts"
    build_step_timeout_sec: float = 1800.0
    build_node_lease_ttl_sec: float = 7200.0

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    def validate_for_runtime(self) -> None:
        """Reject unsafe production defaults before any runtime side effect."""
        if self.env != "prod":
            return

        errors: list[str] = []
        if len(self.jwt_secret) < 32 or self.jwt_secret in {
            "CHANGE-ME-in-prod",
            "CHANGE-ME-32-bytes-min-in-production",
            "change-me-in-prod-please-32bytes-min",
        }:
            errors.append("jwt_secret must be a unique value of at least 32 bytes")
        if self.database_url == "postgresql+asyncpg://yimai:yimai@localhost:5432/yimai":
            errors.append("database_url must not use the development default")
        if self.redis_url == "redis://localhost:6379/0":
            errors.append("redis_url must not use the development default")
        if self.coordination_backend != "redis":
            errors.append("coordination_backend must be redis in production")
        if self.argo_rollouts_enabled and not self.k8s_enabled:
            errors.append("k8s_enabled must be true when argo_rollouts_enabled")
        if self.load_balancer_config:
            if self.load_balancer_config.get("provider") != "http":
                errors.append("load_balancer_config.provider must be http")
            base_url = self.load_balancer_config.get("base_url", "")
            if not base_url.startswith("https://"):
                errors.append("load_balancer_config.base_url must use https in production")
            if not self.load_balancer_config.get("token_credential_id"):
                errors.append("load_balancer_config.token_credential_id is required")
        if not self.cors_origins or all("localhost" in origin for origin in self.cors_origins):
            errors.append("cors_origins must contain a production origin")
        if self.secret_backend != "vault":
            errors.append("secret_backend must be vault in production")
        elif not self.vault_addr or not self.vault_token:
            errors.append("vault_addr and vault_token are required for production")
        if self.seed_admin_user == "admin" or self.seed_admin_password in {"admin", ""}:
            errors.append("seed_admin_user and seed_admin_password must not use defaults")
        if len(self.seed_admin_password) < 12:
            errors.append("seed_admin_password must be at least 12 characters")
        if not self.webhook_secrets:
            errors.append("webhook_secrets must contain at least one source")
        if self.agent_grpc_enabled:
            if not self.agent_grpc_tls_enabled:
                errors.append("agent_grpc_tls_enabled must be true in production")
            for field, value in (
                ("agent_grpc_server_cert_file", self.agent_grpc_server_cert_file),
                ("agent_grpc_server_key_file", self.agent_grpc_server_key_file),
                ("agent_grpc_client_ca_file", self.agent_grpc_client_ca_file),
            ):
                if not value:
                    errors.append(f"{field} is required when Agent mTLS is enabled")
            if not self.agent_grpc_server_address:
                errors.append("agent_grpc_server_address is required when Agent is enabled")
            if self.agent_insecure_install:
                errors.append("agent_insecure_install must be false in production")
        if errors:
            raise ValueError("unsafe production configuration: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()

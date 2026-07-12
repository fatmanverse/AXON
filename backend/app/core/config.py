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

    # 凭证保险箱(§13):local(Fernet)| vault
    secret_backend: Literal["local", "vault"] = "local"
    secret_master_key: str = ""  # local 后端主密钥(生产由 KMS 注入,不落配置文件)
    vault_addr: str = ""
    vault_token: str = ""

    # 任务队列(Celery + Redis)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False

    # 网关限流(T0.12):MVP 用进程内令牌桶,后续可换 Redis 分布式实现
    rate_limit_enabled: bool = True
    rate_limit_capacity: int = 120  # 桶容量(突发上限)
    rate_limit_refill_per_sec: float = 20.0  # 每秒补充速率(稳态 QPS)
    rate_limit_retry_after: int = 1  # 429 响应的 Retry-After 秒数

    # 服务状态采集(T1.12):SSH 轮询补齐间隔(秒);Agent 接入后由心跳取代(§6.1)
    status_collect_interval_sec: float = 30.0

    # 部署轮询兜底(T2.7,§8.2/§8.3④):定时补齐仍卡 running 的部署(webhook 丢失时),
    # 与 webhook 靠状态机去重。间隔(秒);0 或负数关闭。
    deploy_reconcile_interval_sec: float = 60.0

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
    # Agent gRPC server(T4.1,§15.5):Agent 主动外连的监听地址。enabled 关闭时
    # 不起 gRPC server(纯 SSH 部署,默认);开启后 Agent 可建双向流上报/收命令。
    agent_grpc_enabled: bool = False
    agent_grpc_host: str = "0.0.0.0"  # noqa: S104 - Agent 需从各内网机器外连,须监听全网卡
    agent_grpc_port: int = 50051
    # 心跳超时窗(秒):超过未收到心跳判 agent 离线,触发 §5.4 离线分档与 fencing。
    agent_heartbeat_timeout_sec: float = 30.0

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()

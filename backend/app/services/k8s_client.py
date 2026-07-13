"""k8s 客户端生产工厂(T1.10/T3.6 生产接线)。

K8sRuntime 适配器一直只接收注入的 AppsV1ApiLike(测试传 fake),生产从未有人
构造真实 client——这是 k8s 生命周期与发布策略在生产不可用的根因。本模块补上这段:
按 settings 建立集群连接(in-cluster 或 kubeconfig),暴露一个同步工厂
``() -> AppsV1ApiLike``,供 LifecycleService / 发布策略在每次动作时取用 client。

设计要点:
- **kubernetes_asyncio 惰性导入**:仅在 k8s_enabled 且真正加载时 import,未装 client
  的纯裸机环境不受影响(与 K8sRuntime 的 Protocol 抽象同一思路)。该依赖在
  pyproject 声明,生产 ``uv sync`` 时装入。
- **连接配置启动时加载一次**(async,在 lifespan 内),之后工厂是同步的:每次
  ``ApiClient()`` 复用已加载的全局配置,构造轻量 AppsV1Api,不重复读 kubeconfig。
- 加载失败当场抛错(配置错误应在启动期暴露,而非拖到第一次 k8s 部署才炸)。
"""

from __future__ import annotations

from collections.abc import Callable

from app.adapters.k8s_runtime import AppsV1ApiLike
from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("k8s_client")

# 工厂类型:无参构造一个 AppsV1ApiLike(每次动作现取,复用启动时加载的全局连接配置)。
K8sApiFactory = Callable[[], AppsV1ApiLike]


async def build_k8s_api_factory(settings: Settings) -> K8sApiFactory | None:
    """按 settings 加载集群连接并返回同步工厂;未启用 k8s 返回 None。

    in-cluster 用 Pod 的 ServiceAccount;否则加载 kubeconfig(指定路径或默认查找)。
    连接配置加载一次即生效(写入 kubernetes_asyncio 全局 configuration),工厂每次
    只新建一个 AppsV1Api(轻量,复用全局配置)。加载失败上抛,让应用启动即失败。
    """
    if not settings.k8s_enabled:
        return None

    try:
        from kubernetes_asyncio import client, config
    except ImportError as exc:  # pragma: no cover - 仅未装 client 且开启 k8s 时触发
        raise RuntimeError(
            "已开启 k8s_enabled 但未安装 kubernetes_asyncio;请 `uv sync` 安装 k8s 依赖"
        ) from exc

    if settings.k8s_in_cluster:
        config.load_incluster_config()
        log.info("k8s_config_loaded", mode="in_cluster")
    else:
        await config.load_kube_config(config_file=settings.k8s_kubeconfig or None)
        log.info("k8s_config_loaded", mode="kubeconfig", path=settings.k8s_kubeconfig or "default")

    def _factory() -> AppsV1ApiLike:
        # 每次动作新建 AppsV1Api(复用已加载的全局连接配置);AppsV1Api 满足
        # AppsV1ApiLike Protocol(read/patch/scale/delete 方法签名一致)。
        return client.AppsV1Api(client.ApiClient())

    return _factory

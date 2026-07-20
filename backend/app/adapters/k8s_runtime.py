"""k8s 运行时适配(T1.9,设计 §5.1 / §14.2)。

把服务生命周期动作(start/stop/restart/delete/status/scale)翻译成
kubernetes AppsV1 API 调用。与 systemd/docker 适配的关键区别:

- 不经 SSH/Executor,而是走 kubernetes client(AppsV1Api);k8s 服务在
  §14.2 中无 server_id,副本分布实时查不落库,故 status 直接读 API。
- restart 用官方 rollout restart 语义:patch deployment 的
  `kubectl.kubernetes.io/restartedAt` annotation 触发滚动重建,而非删 Pod。
- stop=scale 到 0、start=scale 回目标副本、scale 直接设定副本数——这是 k8s
  中最贴近「停/起一个服务」的动作(delete 才是真正移除工作负载)。
- 所有 API 调用统一捕获异常抛 AppError(code=k8s_action_failed),对上层与
  systemd/docker 的失败语义一致,便于 LifecycleService 统一落 task.failed。

client 通过依赖注入传入(生产传 kubernetes_asyncio.client.AppsV1Api,测试传
fake),本适配器只负责动作到 API 的翻译,不关心集群连接如何建立。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.adapters.executor import DeploySpec, ServiceStatus
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("k8s_runtime")

# rollout restart 的官方 annotation 键(与 kubectl rollout restart 一致)。
_RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


class AppsV1ApiLike(Protocol):
    """kubernetes AppsV1Api 的最小子集(仅本适配器用到的方法)。

    用 Protocol 而非直接依赖 kubernetes_asyncio,既让单测能注入 fake,也避免
    在未装 client 的环境导入即失败(client 仅在生产构造时按需引入)。
    """

    async def read_namespaced_deployment(self, name: str, namespace: str) -> Any: ...
    async def patch_namespaced_deployment(
        self, name: str, namespace: str, body: dict[str, Any]
    ) -> Any: ...
    async def patch_namespaced_deployment_scale(
        self, name: str, namespace: str, body: dict[str, Any]
    ) -> Any: ...
    async def delete_namespaced_deployment(self, name: str, namespace: str) -> Any: ...


def _default_clock() -> datetime:
    return datetime.now(UTC)


class K8sRuntime:
    """k8s Deployment 生命周期动作适配器。

    依赖注入一个 AppsV1ApiLike(生产传真实 client,测试传 fake)与一个 clock
    (便于对 restart annotation 的时间戳做确定性断言)。
    """

    def __init__(
        self,
        api: AppsV1ApiLike,
        *,
        clock: Callable[[], datetime] = _default_clock,
    ) -> None:
        self._api = api
        self._clock = clock

    async def start(self, namespace: str, workload: str, *, replicas: int = 1) -> None:
        """启动服务:scale 回目标副本数(默认 1)。失败抛 AppError。"""
        await self.scale(namespace, workload, replicas)

    async def stop(self, namespace: str, workload: str) -> None:
        """停止服务:scale 到 0(保留 Deployment 定义,可再拉起)。失败抛 AppError。"""
        await self.scale(namespace, workload, 0)

    async def scale(self, namespace: str, workload: str, replicas: int) -> None:
        """设定副本数。失败抛 AppError。"""
        await self._call(
            "scale",
            workload,
            self._api.patch_namespaced_deployment_scale(
                name=workload,
                namespace=namespace,
                body={"spec": {"replicas": replicas}},
            ),
        )

    async def restart(self, namespace: str, workload: str) -> None:
        """滚动重启:patch restartedAt annotation 触发 rollout。失败抛 AppError。"""
        body = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {_RESTART_ANNOTATION: self._clock().isoformat()}}
                }
            }
        }
        await self._call(
            "restart",
            workload,
            self._api.patch_namespaced_deployment(name=workload, namespace=namespace, body=body),
        )

    async def delete(self, namespace: str, workload: str) -> None:
        """下线服务:删除 Deployment(真正移除工作负载)。失败抛 AppError。"""
        await self._call(
            "delete",
            workload,
            self._api.delete_namespaced_deployment(name=workload, namespace=namespace),
        )

    async def status(self, namespace: str, workload: str) -> ServiceStatus:
        """查询状态:读 Deployment 副本数,ready_replicas>0 视为 running。

        与 systemd/docker 的 status 不同,k8s 的 API 报错(如 Deployment 不存在)
        本身即为异常路径,故这里抛 AppError 而非静默——上层能区分「查询失败」与
        「确实未就绪」。副本分布实时查,不落库(§14.2)。
        """
        try:
            deployment = await self._api.read_namespaced_deployment(
                name=workload, namespace=namespace
            )
        except Exception as exc:
            raise self._failure("status", workload, exc) from exc

        desired = deployment.spec.replicas or 0
        ready = deployment.status.ready_replicas or 0
        return ServiceStatus(
            name=workload,
            running=ready > 0,
            detail=f"{ready}/{desired} ready",
        )

    async def deploy(self, spec: DeploySpec) -> None:
        """发布制品:patch Deployment 首个容器的镜像(set-image 触发滚动更新)。

        与 systemd/docker 的 deploy 不同,k8s 不 pull/run——把目标镜像 patch 进
        Deployment 模板,由集群按现有滚动策略自行拉取新镜像、逐步替换 Pod。
        namespace/workload 定位工作负载,image 为目标镜像坐标(含 tag/digest)。
        失败抛 AppError(k8s_action_failed),与生命周期动作失败语义一致。
        """
        if not spec.namespace or not spec.workload:
            raise AppError(
                "invalid_runtime_ref",
                "k8s 部署需 namespace 与 workload",
                status_code=400,
            )
        if not spec.image:
            raise AppError(
                "invalid_deploy_spec",
                "k8s 部署需 image(目标镜像坐标)",
                status_code=400,
            )
        # JSON Patch 按索引只改首个容器镜像,不假设容器名与 Deployment 同名。
        body = [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/image",
                "value": spec.image,
            }
        ]
        await self._call(
            "deploy",
            spec.workload,
            self._api.patch_namespaced_deployment(
                name=spec.workload,
                namespace=spec.namespace,
                body=body,
                _content_type="application/json-patch+json",
            ),
        )

    async def _call(self, action: str, workload: str, coro: Any) -> None:
        """await 一个 API 协程,任何异常翻译为统一的 AppError。"""
        try:
            await coro
        except Exception as exc:
            raise self._failure(action, workload, exc) from exc

    def _failure(self, action: str, workload: str, exc: Exception) -> AppError:
        # 只在服务端日志留 workload 与异常类型,message 透出原因供上层定位
        log.warning(
            "k8s_action_failed",
            action=action,
            workload=workload,
            error_type=type(exc).__name__,
        )
        return AppError(
            "k8s_action_failed",
            f"k8s {action} 失败({workload}): {exc}",
            status_code=502,
        )

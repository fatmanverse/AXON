"""Argo Rollouts provider for canary/blue-green promotion."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("argo_rollouts")


class CustomObjectsApiLike(Protocol):
    async def patch_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str, body: dict[str, Any]
    ) -> Any: ...

    async def get_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str
    ) -> dict[str, Any]: ...


class ArgoRolloutsProvider:
    """通过 Argo Rollouts annotation 控制 promote/abort，并等待健康状态。"""

    def __init__(
        self,
        api: CustomObjectsApiLike,
        *,
        group: str = "argoproj.io",
        version: str = "v1alpha1",
        plural: str = "rollouts",
        timeout_sec: float = 300.0,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self._api = api
        self._group = group
        self._version = version
        self._plural = plural
        self._timeout = timeout_sec
        self._poll = poll_interval_sec

    async def promote(self, namespace: str, workload: str) -> None:
        await self._operation(namespace, workload, "promote")
        await self.wait_healthy(namespace, workload)

    async def abort(self, namespace: str, workload: str) -> None:
        await self._operation(namespace, workload, "abort")

    async def rollback(self, namespace: str, workload: str) -> None:
        await self.abort(namespace, workload)

    async def wait_healthy(self, namespace: str, workload: str) -> None:
        deadline = asyncio.get_running_loop().time() + self._timeout
        while True:
            try:
                rollout = await self._api.get_namespaced_custom_object(
                    self._group, self._version, namespace, self._plural, workload
                )
            except Exception as exc:
                raise self._failure("status", workload, exc) from exc
            phase = str((rollout.get("status") or {}).get("phase") or "").lower()
            if phase in {"healthy", "completed"}:
                return
            if phase in {"degraded", "error", "abort"}:
                raise AppError(
                    "rollout_unhealthy",
                    f"Argo Rollout {workload} 状态为 {phase}",
                    status_code=502,
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise AppError(
                    "rollout_health_timeout",
                    f"Argo Rollout {workload} 健康检查超时({self._timeout}s)",
                    status_code=504,
                )
            await asyncio.sleep(self._poll)

    async def _operation(self, namespace: str, workload: str, operation: str) -> None:
        body = {"metadata": {"annotations": {"rollouts.argoproj.io/operation": operation}}}
        try:
            await self._api.patch_namespaced_custom_object(
                self._group,
                self._version,
                namespace,
                self._plural,
                workload,
                body,
            )
        except Exception as exc:
            raise self._failure(operation, workload, exc) from exc

    def _failure(self, action: str, workload: str, exc: Exception) -> AppError:
        log.warning("argo_rollout_failed", action=action, workload=workload)
        return AppError(
            "argo_rollout_failed",
            f"Argo Rollout {action} 失败({workload}): {exc}",
            status_code=502,
        )

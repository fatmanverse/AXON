"""Explicit HTTP LoadBalancer provider for bare-metal canary/blue-green."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.core.errors import AppError


class SecretReader(Protocol):
    def get(self, credential_id: str) -> str: ...


class HttpLoadBalancer:
    """对接企业 LB adapter API，不执行任意 shell 或隐式 nginx 修改。"""

    def __init__(
        self,
        base_url: str,
        secrets: SecretReader,
        *,
        token_credential_id: str = "",
        timeout_sec: float = 10.0,
        client: Any | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("LoadBalancer base_url is required")
        self._base_url = base_url.rstrip("/")
        self._secrets = secrets
        self._token_id = token_credential_id
        self._timeout = timeout_sec
        self._client = client

    async def set_weight(self, target: str, weight: int) -> None:
        if weight < 0 or weight > 100:
            raise AppError("load_balancer_invalid_weight", "LB 权重必须在 0..100", status_code=400)
        await self._post("/weights", {"target": target, "weight": weight})

    async def switch_upstream(self, target: str, upstream: str) -> None:
        await self._post("/switch", {"target": target, "upstream": upstream})

    async def _post(self, path: str, payload: dict[str, str | int]) -> None:
        client = None
        close = False
        try:
            headers: dict[str, str] = {}
            if self._token_id:
                headers["Authorization"] = f"Bearer {self._secrets.get(self._token_id)}"
            client = self._client or httpx.AsyncClient(timeout=self._timeout)
            close = self._client is None
            response = await client.post(f"{self._base_url}{path}", json=payload, headers=headers)
            response.raise_for_status()
        except Exception as exc:
            raise AppError(
                "load_balancer_request_failed",
                f"LoadBalancer 请求失败: {exc}",
                status_code=502,
            ) from exc
        finally:
            if close and client is not None:
                await client.aclose()

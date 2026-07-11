"""CI 流水线适配层(T2.2,设计 §8.1)。

统一接口 PipelineAdapter 封装各 CI 的触发/查状态/取日志,上层(部署编排)只依赖
接口,不关心 Jenkins 与 GitLab 的 API 差异:

- Jenkins: POST /job/{job}/buildWithParameters 触发;GET /job/{job}/{n}/api/json 查状态。
- GitLab:  POST /api/v4/projects/{id}/trigger/pipeline(trigger token)触发;
           GET /api/v4/projects/{id}/pipelines/{id} 查状态。

安全:平台 token/trigger token 从凭证保险箱按 id 取(§13),不硬编码、不落业务表。
http client 依赖注入(生产传 httpx.AsyncClient,测试传 fake),HTTP 层故障统一归一
为 AppError(pipeline_unavailable),不泄漏底层异常原文(§security)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Protocol

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.secrets import SecretStore

log = get_logger("pipeline")

DEFAULT_TIMEOUT = 15.0


class PipelineRunStatus(StrEnum):
    """归一化的流水线运行状态。各 CI 的原始状态映射到这四态。"""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


class HttpClientLike(Protocol):
    """httpx.AsyncClient 的最小子集(request + async 上下文)。"""

    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...
    async def __aenter__(self) -> HttpClientLike: ...
    async def __aexit__(self, *exc: Any) -> None: ...


class PipelineAdapter(ABC):
    """CI 流水线统一接口(§8.1):触发、查状态、取日志。"""

    @abstractmethod
    async def trigger(self, ref: str, *, params: dict[str, str]) -> str | None:
        """触发一次流水线。返回运行 id(若 CI 即时给出),否则 None。"""

    @abstractmethod
    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus: ...

    @abstractmethod
    async def get_logs(self, ref: str, *, run_id: str) -> str: ...


def _build_client() -> HttpClientLike:
    import httpx

    return httpx.AsyncClient()


class _BaseHttpAdapter(PipelineAdapter):
    """承载注入的 http client 与统一的请求/错误归一逻辑。"""

    def __init__(
        self,
        *,
        secret_store: SecretStore,
        http_client: HttpClientLike | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._secrets = secret_store
        self._http = http_client
        self._timeout = timeout

    async def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            http = self._http if self._http is not None else _build_client()
            async with http as conn:
                return await conn.request(method, url, timeout=self._timeout, **kwargs)
        except AppError:
            raise
        except Exception as exc:
            log.warning(
                "pipeline_request_failed",
                method=method,
                error_type=type(exc).__name__,
            )
            raise AppError(
                "pipeline_unavailable",
                "CI 流水线后端暂不可用",
                status_code=502,
            ) from exc


class JenkinsPipelineAdapter(_BaseHttpAdapter):
    """Jenkins 实现:buildWithParameters 触发,api/json 查状态。"""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        token_credential_id: str,
        secret_store: SecretStore,
        http_client: HttpClientLike | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(secret_store=secret_store, http_client=http_client, timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._token_id = token_credential_id

    def _auth(self) -> tuple[str, str]:
        # token 从保险箱取,不缓存明文
        return (self._username, self._secrets.get(self._token_id))

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str | None:
        url = f"{self._base_url}/job/{ref}/buildWithParameters"
        await self._request("POST", url, params=params, auth=self._auth())
        # Jenkins 触发后运行号在 queue 里异步分配,MVP 不解析 Location,返回 None
        return None

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        url = f"{self._base_url}/job/{ref}/{run_id}/api/json"
        resp = await self._request("GET", url, auth=self._auth())
        data = resp.json()
        if data.get("building"):
            return PipelineRunStatus.RUNNING
        return _map_jenkins_result(data.get("result"))

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        url = f"{self._base_url}/job/{ref}/{run_id}/consoleText"
        resp = await self._request("GET", url, auth=self._auth())
        return resp.text


class GitLabPipelineAdapter(_BaseHttpAdapter):
    """GitLab 实现:trigger token 触发 pipeline,pipelines/{id} 查状态。"""

    def __init__(
        self,
        *,
        base_url: str,
        project_id: str,
        trigger_token_credential_id: str,
        secret_store: SecretStore,
        http_client: HttpClientLike | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(secret_store=secret_store, http_client=http_client, timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._project_id = project_id
        self._token_id = trigger_token_credential_id

    async def trigger(self, ref: str, *, params: dict[str, str]) -> str | None:
        url = f"{self._base_url}/api/v4/projects/{self._project_id}/trigger/pipeline"
        token = self._secrets.get(self._token_id)
        data: dict[str, str] = {"token": token, "ref": ref}
        # CI/CD 变量以 variables[KEY]=VALUE 形式随 trigger 传入
        for key, value in params.items():
            data[f"variables[{key}]"] = value
        resp = await self._request("POST", url, data=data)
        body = resp.json()
        run_id = body.get("id")
        return str(run_id) if run_id is not None else None

    async def get_status(self, ref: str, *, run_id: str) -> PipelineRunStatus:
        url = f"{self._base_url}/api/v4/projects/{self._project_id}/pipelines/{run_id}"
        resp = await self._request("GET", url)
        return _map_gitlab_status(resp.json().get("status"))

    async def get_logs(self, ref: str, *, run_id: str) -> str:
        # GitLab 的日志按 job 分散,pipeline 级无单一 trace;MVP 返回提示,细化留后续。
        return f"GitLab pipeline {run_id} 的日志需按 job 逐个拉取(MVP 未实现聚合)。"


# Jenkins result 字段 → 归一状态。null/ABORTED 等未知情形落 UNKNOWN。
_JENKINS_RESULT = {
    "SUCCESS": PipelineRunStatus.SUCCESS,
    "FAILURE": PipelineRunStatus.FAILED,
    "UNSTABLE": PipelineRunStatus.FAILED,
}

# GitLab pipeline status → 归一状态。
_GITLAB_STATUS = {
    "created": PipelineRunStatus.RUNNING,
    "waiting_for_resource": PipelineRunStatus.RUNNING,
    "preparing": PipelineRunStatus.RUNNING,
    "pending": PipelineRunStatus.RUNNING,
    "running": PipelineRunStatus.RUNNING,
    "scheduled": PipelineRunStatus.RUNNING,
    "success": PipelineRunStatus.SUCCESS,
    "failed": PipelineRunStatus.FAILED,
    "canceled": PipelineRunStatus.FAILED,
    "skipped": PipelineRunStatus.FAILED,
}


def _map_jenkins_result(result: str | None) -> PipelineRunStatus:
    if result is None:
        return PipelineRunStatus.UNKNOWN
    return _JENKINS_RESULT.get(result, PipelineRunStatus.UNKNOWN)


def _map_gitlab_status(status: str | None) -> PipelineRunStatus:
    if status is None:
        return PipelineRunStatus.UNKNOWN
    return _GITLAB_STATUS.get(status, PipelineRunStatus.UNKNOWN)

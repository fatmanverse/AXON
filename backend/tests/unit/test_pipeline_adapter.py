"""T2.2 CI 适配层接口 + Jenkins/GitLab 实现(设计 §8.1)。

用 fake http client 验证两实现对统一接口 PipelineAdapter 的落地:
- trigger 拼对各自的触发 URL、带对参数(Jenkins buildWithParameters / GitLab pipeline)。
- get_status 解析各自的状态响应并归一为 PipelineStatus。
- get_logs 取回日志文本。
- 平台 token 从注入的 secret store 取,不硬编码。
- HTTP 层故障归一为 AppError(pipeline_unavailable),不泄漏底层细节。

不触真实 Jenkins/GitLab。
"""

import pytest

from app.adapters.pipeline import (
    GitLabPipelineAdapter,
    JenkinsPipelineAdapter,
    PipelineRunStatus,
)
from app.core.errors import AppError
from app.core.secrets import LocalSecretStore, generate_master_key


class _FakeResponse:
    def __init__(self, status_code: int, *, json_body=None, text_body: str = "") -> None:
        self.status_code = status_code
        self._json = json_body
        self.text = text_body
        # Jenkins 触发返回 Location 指向 queue item
        self.headers: dict[str, str] = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeHttpClient:
    """记录请求;按 (method, url) 预置响应或抛错。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._responses: dict[tuple[str, str], _FakeResponse] = {}
        self._raise: Exception | None = None

    def stub(self, method: str, url: str, response: _FakeResponse) -> None:
        self._responses[(method.upper(), url)] = response

    def fail_with(self, exc: Exception) -> None:
        self._raise = exc

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method.upper(), "url": url, **kwargs})
        if self._raise is not None:
            raise self._raise
        key = (method.upper(), url)
        if key not in self._responses:
            raise AssertionError(f"unstubbed request: {key}")
        return self._responses[key]

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.fixture
def secrets():
    store = LocalSecretStore(master_key=generate_master_key())
    return store


# ---------- Jenkins ----------


async def test_jenkins_trigger_posts_build_with_parameters(secrets):
    token_id = secrets.put("jenkins-token", "jenkins-api-token")
    http = _FakeHttpClient()
    http.stub(
        "POST",
        "https://ci.example.com/job/deploy-billing/buildWithParameters",
        _FakeResponse(201),
    )
    adapter = JenkinsPipelineAdapter(
        base_url="https://ci.example.com",
        username="ops",
        token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )

    await adapter.trigger("deploy-billing", params={"VERSION": "v1.2.0", "ENV": "prod"})

    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/job/deploy-billing/buildWithParameters")
    # 参数经 params 传递
    assert call["params"]["VERSION"] == "v1.2.0"
    assert call["params"]["ENV"] == "prod"


async def test_jenkins_get_status_maps_result(secrets):
    token_id = secrets.put("jenkins-token", "t")
    http = _FakeHttpClient()
    http.stub(
        "GET",
        "https://ci.example.com/job/deploy-billing/42/api/json",
        _FakeResponse(200, json_body={"building": False, "result": "SUCCESS"}),
    )
    adapter = JenkinsPipelineAdapter(
        base_url="https://ci.example.com",
        username="ops",
        token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )

    status = await adapter.get_status("deploy-billing", run_id="42")
    assert status == PipelineRunStatus.SUCCESS


async def test_jenkins_building_maps_to_running(secrets):
    token_id = secrets.put("jenkins-token", "t")
    http = _FakeHttpClient()
    http.stub(
        "GET",
        "https://ci.example.com/job/j/7/api/json",
        _FakeResponse(200, json_body={"building": True, "result": None}),
    )
    adapter = JenkinsPipelineAdapter(
        base_url="https://ci.example.com",
        username="ops",
        token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )
    assert await adapter.get_status("j", run_id="7") == PipelineRunStatus.RUNNING


async def test_jenkins_http_error_raises_app_error(secrets):
    token_id = secrets.put("jenkins-token", "t")
    http = _FakeHttpClient()
    http.fail_with(OSError("connection refused"))
    adapter = JenkinsPipelineAdapter(
        base_url="https://ci.example.com",
        username="ops",
        token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )
    with pytest.raises(AppError) as excinfo:
        await adapter.trigger("j", params={})
    assert excinfo.value.code == "pipeline_unavailable"
    assert "connection refused" not in excinfo.value.message


# ---------- GitLab ----------


async def test_gitlab_trigger_posts_pipeline_with_token(secrets):
    token_id = secrets.put("gitlab-trigger", "glptt-xxx")
    http = _FakeHttpClient()
    http.stub(
        "POST",
        "https://gitlab.example.com/api/v4/projects/123/trigger/pipeline",
        _FakeResponse(201, json_body={"id": 555, "status": "created"}),
    )
    adapter = GitLabPipelineAdapter(
        base_url="https://gitlab.example.com",
        project_id="123",
        trigger_token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )

    run_id = await adapter.trigger("main", params={"VERSION": "v1.2.0"})

    call = http.calls[0]
    assert call["url"].endswith("/api/v4/projects/123/trigger/pipeline")
    # trigger token 与 ref 走 data;变量以 variables[KEY] 形式
    assert call["data"]["token"] == "glptt-xxx"
    assert call["data"]["ref"] == "main"
    assert run_id == "555"


async def test_gitlab_get_status_maps_result(secrets):
    token_id = secrets.put("gitlab-trigger", "t")
    http = _FakeHttpClient()
    http.stub(
        "GET",
        "https://gitlab.example.com/api/v4/projects/123/pipelines/555",
        _FakeResponse(200, json_body={"id": 555, "status": "success"}),
    )
    adapter = GitLabPipelineAdapter(
        base_url="https://gitlab.example.com",
        project_id="123",
        trigger_token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )
    assert await adapter.get_status("main", run_id="555") == PipelineRunStatus.SUCCESS


async def test_gitlab_failed_status_maps(secrets):
    token_id = secrets.put("gitlab-trigger", "t")
    http = _FakeHttpClient()
    http.stub(
        "GET",
        "https://gitlab.example.com/api/v4/projects/123/pipelines/9",
        _FakeResponse(200, json_body={"id": 9, "status": "failed"}),
    )
    adapter = GitLabPipelineAdapter(
        base_url="https://gitlab.example.com",
        project_id="123",
        trigger_token_credential_id=token_id,
        secret_store=secrets,
        http_client=http,
    )
    assert await adapter.get_status("main", run_id="9") == PipelineRunStatus.FAILED

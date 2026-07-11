"""T1.9 k8s 运行时适配(基础)。

与 systemd/docker 不同,k8s 不经 SSH/Executor,而是走 kubernetes client。
用 fake client 记录调用,验证:
- restart 走 rollout restart(patch deployment 的 restartedAt annotation)
- stop=scale 0、start=scale 到 runtime_ref.replicas、scale 直接设定副本
- delete 删除 deployment
- status 读 deployment 副本数解析 running(ready_replicas>0),实时查不落库
- namespace/workload 正确透传;api 报错抛 AppError

单测不触碰真实集群。
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.adapters.executor import ServiceStatus
from app.adapters.k8s_runtime import K8sRuntime
from app.core.errors import AppError

NS = "billing-prod"
WORKLOAD = "billing"
_FIXED_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


class FakeAppsV1Api:
    """记录调用的假 kubernetes AppsV1Api。read 返回预置 deployment。"""

    def __init__(self, *, deployment=None, fail: bool = False) -> None:
        self._deployment = deployment
        self._fail = fail
        self.patched: list[dict] = []
        self.scaled: list[dict] = []
        self.deleted: list[dict] = []
        self.read_calls: list[dict] = []

    async def read_namespaced_deployment(self, name: str, namespace: str):
        self.read_calls.append({"name": name, "namespace": namespace})
        if self._fail:
            raise RuntimeError("api server unreachable")
        return self._deployment

    async def patch_namespaced_deployment(self, name: str, namespace: str, body: dict):
        if self._fail:
            raise RuntimeError("patch rejected")
        self.patched.append({"name": name, "namespace": namespace, "body": body})

    async def patch_namespaced_deployment_scale(self, name: str, namespace: str, body: dict):
        if self._fail:
            raise RuntimeError("scale rejected")
        self.scaled.append({"name": name, "namespace": namespace, "body": body})

    async def delete_namespaced_deployment(self, name: str, namespace: str):
        if self._fail:
            raise RuntimeError("delete rejected")
        self.deleted.append({"name": name, "namespace": namespace})


def _deployment(*, desired: int = 3, ready: int | None = 3):
    """构造与 kubernetes client V1Deployment 同构的最小对象。"""
    return SimpleNamespace(
        spec=SimpleNamespace(replicas=desired),
        status=SimpleNamespace(replicas=desired, ready_replicas=ready, available_replicas=ready),
    )


def _runtime(api: FakeAppsV1Api) -> K8sRuntime:
    return K8sRuntime(api, clock=lambda: _FIXED_NOW)


async def test_restart_patches_restarted_at_annotation():
    api = FakeAppsV1Api()
    await _runtime(api).restart(NS, WORKLOAD)

    assert len(api.patched) == 1
    patch = api.patched[0]
    assert patch["name"] == WORKLOAD
    assert patch["namespace"] == NS
    annotations = patch["body"]["spec"]["template"]["metadata"]["annotations"]
    assert annotations["kubectl.kubernetes.io/restartedAt"] == _FIXED_NOW.isoformat()


async def test_stop_scales_to_zero():
    api = FakeAppsV1Api()
    await _runtime(api).stop(NS, WORKLOAD)

    assert api.scaled == [
        {"name": WORKLOAD, "namespace": NS, "body": {"spec": {"replicas": 0}}}
    ]


async def test_start_scales_to_target_replicas():
    api = FakeAppsV1Api()
    await _runtime(api).start(NS, WORKLOAD, replicas=4)

    assert api.scaled == [
        {"name": WORKLOAD, "namespace": NS, "body": {"spec": {"replicas": 4}}}
    ]


async def test_scale_sets_replicas():
    api = FakeAppsV1Api()
    await _runtime(api).scale(NS, WORKLOAD, 7)

    assert api.scaled == [
        {"name": WORKLOAD, "namespace": NS, "body": {"spec": {"replicas": 7}}}
    ]


async def test_delete_removes_deployment():
    api = FakeAppsV1Api()
    await _runtime(api).delete(NS, WORKLOAD)

    assert api.deleted == [{"name": WORKLOAD, "namespace": NS}]


async def test_status_ready_replicas_reports_running():
    api = FakeAppsV1Api(deployment=_deployment(desired=3, ready=3))
    status = await _runtime(api).status(NS, WORKLOAD)

    assert isinstance(status, ServiceStatus)
    assert status.name == WORKLOAD
    assert status.running is True
    assert "3/3" in status.detail
    assert api.read_calls == [{"name": WORKLOAD, "namespace": NS}]


async def test_status_zero_ready_reports_not_running():
    api = FakeAppsV1Api(deployment=_deployment(desired=3, ready=0))
    status = await _runtime(api).status(NS, WORKLOAD)

    assert status.running is False
    assert "0/3" in status.detail


async def test_status_none_ready_replicas_treated_as_zero():
    """ready_replicas 为 None(刚创建/全挂)时按 0 处理,不抛错。"""
    api = FakeAppsV1Api(deployment=_deployment(desired=2, ready=None))
    status = await _runtime(api).status(NS, WORKLOAD)

    assert status.running is False
    assert "0/2" in status.detail


@pytest.mark.parametrize(
    "action,args",
    [
        ("restart", (NS, WORKLOAD)),
        ("stop", (NS, WORKLOAD)),
        ("delete", (NS, WORKLOAD)),
        ("scale", (NS, WORKLOAD, 2)),
    ],
)
async def test_action_raises_app_error_on_api_failure(action: str, args: tuple):
    api = FakeAppsV1Api(fail=True)
    runtime = _runtime(api)

    with pytest.raises(AppError) as excinfo:
        await getattr(runtime, action)(*args)

    assert excinfo.value.code == "k8s_action_failed"


async def test_status_raises_app_error_on_api_failure():
    api = FakeAppsV1Api(fail=True)

    with pytest.raises(AppError) as excinfo:
        await _runtime(api).status(NS, WORKLOAD)

    assert excinfo.value.code == "k8s_action_failed"

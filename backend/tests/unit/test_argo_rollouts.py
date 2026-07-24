import pytest

from app.adapters.argo_rollouts import ArgoRolloutsProvider
from app.core.errors import AppError


class _FakeCustomObjects:
    def __init__(self, phase: str = "Healthy") -> None:
        self.phase = phase
        self.patches: list[dict] = []

    async def patch_namespaced_custom_object(self, *args, **kwargs):
        self.patches.append({"args": args, "kwargs": kwargs})

    async def get_namespaced_custom_object(self, *args, **kwargs):
        return {"status": {"phase": self.phase}}


async def test_argo_promote_patches_operation_and_waits_healthy():
    api = _FakeCustomObjects()
    provider = ArgoRolloutsProvider(api, poll_interval_sec=0)

    await provider.promote("prod", "billing")

    assert api.patches[0]["args"][-1] == {
        "metadata": {"annotations": {"rollouts.argoproj.io/operation": "promote"}}
    }


async def test_argo_degraded_status_is_typed_failure():
    api = _FakeCustomObjects(phase="Degraded")
    provider = ArgoRolloutsProvider(api, poll_interval_sec=0)

    with pytest.raises(AppError, match="degraded") as exc:
        await provider.promote("prod", "billing")

    assert exc.value.code == "rollout_unhealthy"

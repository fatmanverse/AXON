from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.models.build_node import BuildNodeStatus
from app.services.build_node_scheduler import BuildNodeScheduler


def _node(name: str, *, labels=None, max_concurrent=1):
    return SimpleNamespace(
        id=name,
        status=BuildNodeStatus.ONLINE,
        labels=labels or {},
        max_concurrent=max_concurrent,
    )


async def test_scheduler_matches_labels_and_releases_memory_slot():
    scheduler = BuildNodeScheduler(None)
    node, slot = await scheduler.acquire(
        [_node("n1", labels={"go": "1.22"})],
        required_labels={"go": "1.22"},
    )
    assert node.id == "n1"
    with pytest.raises(AppError, match="没有满足"):
        await scheduler.acquire([_node("n1", labels={"go": "1.22"})])
    await slot.release()


async def test_scheduler_rejects_offline_and_mismatched_nodes():
    scheduler = BuildNodeScheduler(None)
    offline = _node("offline", labels={"node": "x"})
    offline.status = BuildNodeStatus.OFFLINE

    with pytest.raises(AppError) as exc:
        await scheduler.acquire(
            [offline, _node("wrong", labels={"node": "y"})], required_labels={"node": "x"}
        )

    assert exc.value.code == "build_capacity_unavailable"

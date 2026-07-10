"""T0.3 tasks 状态机(§14.6)。pending/running/success/failed/unknown。"""

import pytest

from app.models.task import TaskStatus, can_transition, ensure_transition


@pytest.mark.parametrize(
    "src,dst",
    [
        (TaskStatus.PENDING, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.SUCCESS),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.UNKNOWN),  # 超时/断连(§5.4)
        (TaskStatus.UNKNOWN, TaskStatus.SUCCESS),  # 重连核对后落定
        (TaskStatus.UNKNOWN, TaskStatus.FAILED),
    ],
)
def test_valid_transitions(src, dst):
    assert can_transition(src, dst) is True


@pytest.mark.parametrize(
    "src,dst",
    [
        (TaskStatus.PENDING, TaskStatus.SUCCESS),  # 必须先 running
        (TaskStatus.SUCCESS, TaskStatus.RUNNING),  # 终态不可回退
        (TaskStatus.FAILED, TaskStatus.RUNNING),
        (TaskStatus.SUCCESS, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.PENDING),  # 不可回退
    ],
)
def test_invalid_transitions(src, dst):
    assert can_transition(src, dst) is False


def test_ensure_transition_raises_on_invalid():
    with pytest.raises(ValueError, match="非法状态流转"):
        ensure_transition(TaskStatus.SUCCESS, TaskStatus.RUNNING)


def test_terminal_states():
    assert TaskStatus.SUCCESS.is_terminal()
    assert TaskStatus.FAILED.is_terminal()
    assert not TaskStatus.PENDING.is_terminal()
    assert not TaskStatus.RUNNING.is_terminal()
    # unknown 非终态:待核对(§5.4)
    assert not TaskStatus.UNKNOWN.is_terminal()

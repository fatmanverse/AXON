"""T2.1 deployments 状态机(§14.3)。

状态机规则:running → success / failed / rolled_back;三者均为终态,不可再
转出(部署记录只前进不回退)。rolled_back 是"被回滚闭环"的终态(§11.2)。
"""

import pytest

from app.models.deployment import (
    DeploymentSource,
    DeploymentStatus,
    DeploymentStrategy,
    can_transition,
    ensure_transition,
)


def test_running_can_reach_each_terminal():
    for terminal in (
        DeploymentStatus.SUCCESS,
        DeploymentStatus.FAILED,
        DeploymentStatus.ROLLED_BACK,
    ):
        assert can_transition(DeploymentStatus.RUNNING, terminal)


@pytest.mark.parametrize(
    "terminal",
    [DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK],
)
def test_terminal_states_are_frozen(terminal):
    # failed / rolled_back 为完全终态,不可再转出到任何状态(含自身)
    for dst in DeploymentStatus:
        assert not can_transition(terminal, dst)


def test_success_can_only_transition_to_rolled_back():
    # success 是"运行中的成功版",被回滚时转 rolled_back(§11.2 闭环),
    # 但不能回退到 running,也不能转 failed 或停在自身。
    assert can_transition(DeploymentStatus.SUCCESS, DeploymentStatus.ROLLED_BACK)
    assert not can_transition(DeploymentStatus.SUCCESS, DeploymentStatus.RUNNING)
    assert not can_transition(DeploymentStatus.SUCCESS, DeploymentStatus.FAILED)
    assert not can_transition(DeploymentStatus.SUCCESS, DeploymentStatus.SUCCESS)


def test_running_cannot_stay_running():
    # running→running 无意义,不允许(须落一个终态)
    assert not can_transition(DeploymentStatus.RUNNING, DeploymentStatus.RUNNING)


def test_ensure_transition_raises_on_illegal():
    with pytest.raises(ValueError, match="非法状态流转"):
        ensure_transition(DeploymentStatus.SUCCESS, DeploymentStatus.RUNNING)


def test_ensure_transition_passes_on_legal():
    # 合法流转不抛
    ensure_transition(DeploymentStatus.RUNNING, DeploymentStatus.SUCCESS)


def test_status_is_terminal():
    assert DeploymentStatus.SUCCESS.is_terminal()
    assert DeploymentStatus.FAILED.is_terminal()
    assert DeploymentStatus.ROLLED_BACK.is_terminal()
    assert not DeploymentStatus.RUNNING.is_terminal()


def test_enum_values_cover_design():
    # 与设计 §14.3 对齐:strategy 四选一、source 三选一
    assert {s.value for s in DeploymentStrategy} == {
        "rolling",
        "canary",
        "blue-green",
        "recreate",
    }
    assert {s.value for s in DeploymentSource} == {
        "ui-triggered",
        "pipeline-webhook",
        "manual",
    }

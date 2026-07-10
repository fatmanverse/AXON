"""T0.4 权限匹配:(resource, env, action) 三元组 + 通配。

接口形态定死(§10.2/§13),MVP 权限点可粗(用通配),后续细化不改调用方。
"""

import pytest

from app.core.permissions import Permission, PermissionSet, parse_permission


def test_parse_permission_triple():
    p = parse_permission("service:prod:delete")
    assert p == Permission(resource="service", env="prod", action="delete")


def test_parse_rejects_bad_format():
    with pytest.raises(ValueError):
        parse_permission("service:delete")  # 缺 env 段


@pytest.mark.parametrize(
    "granted,required,ok",
    [
        ("*:*:*", "service:prod:delete", True),  # 超管通配
        ("service:*:*", "service:prod:delete", True),  # 资源级放开所有环境/动作
        ("service:prod:*", "service:prod:delete", True),
        ("service:prod:delete", "service:prod:delete", True),
        ("service:dev:*", "service:prod:delete", False),  # 环境不匹配
        ("server:*:*", "service:prod:delete", False),  # 资源不匹配
        ("service:prod:restart", "service:prod:delete", False),  # 动作不匹配
    ],
)
def test_permission_match(granted, required, ok):
    pset = PermissionSet([parse_permission(granted)])
    assert pset.allows(parse_permission(required)) is ok


def test_permission_set_union():
    pset = PermissionSet(
        [parse_permission("service:dev:*"), parse_permission("service:staging:deploy")]
    )
    assert pset.allows(parse_permission("service:dev:delete"))
    assert pset.allows(parse_permission("service:staging:deploy"))
    assert not pset.allows(parse_permission("service:prod:deploy"))

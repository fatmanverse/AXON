"""RBAC 权限模型:(resource, env, action) 三元组 + 通配匹配。

接口形态定死(§10.2/§13):所有鉴权点都表达为"对某资源、某环境、某动作"的授权。
MVP 阶段角色可授粗粒度通配权限(如 admin=*:*:*),后续细化权限点不改调用方。
"""

from dataclasses import dataclass

_WILDCARD = "*"


@dataclass(frozen=True)
class Permission:
    resource: str
    env: str
    action: str

    def __str__(self) -> str:
        return f"{self.resource}:{self.env}:{self.action}"


def parse_permission(raw: str) -> Permission:
    parts = raw.split(":")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"权限格式应为 resource:env:action,收到: {raw!r}")
    resource, env, action = parts
    return Permission(resource=resource, env=env, action=action)


def _segment_matches(granted: str, required: str) -> bool:
    return granted == _WILDCARD or granted == required


class PermissionSet:
    """一个主体持有的权限集合;allows 判断是否覆盖某个所需权限。"""

    def __init__(self, permissions: list[Permission]) -> None:
        self._permissions = tuple(permissions)

    @property
    def permissions(self) -> tuple[Permission, ...]:
        return self._permissions

    def allows(self, required: Permission) -> bool:
        return any(
            _segment_matches(g.resource, required.resource)
            and _segment_matches(g.env, required.env)
            and _segment_matches(g.action, required.action)
            for g in self._permissions
        )

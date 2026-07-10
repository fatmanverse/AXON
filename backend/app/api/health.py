"""健康检查端点。

/healthz 汇总各依赖组件探活结果。组件通过 register_probe 登记探活协程,
T0.2 接入 DB、T0.3 接入 Redis 时各自登记,健康检查无需改动本模块。
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter

from app.core.responses import ok

router = APIRouter(tags=["health"])

Probe = Callable[[], Awaitable[None]]

# 组件探活注册表:名称 -> 探活协程(探活失败抛异常即判 down)。
_CHECKS: dict[str, Probe] = {}


def register_probe(name: str, probe: Probe) -> None:
    _CHECKS[name] = probe


def unregister_probe(name: str) -> None:
    _CHECKS.pop(name, None)


@router.get("/healthz")
async def healthz() -> dict:
    checks: dict[str, str] = {}
    for name, probe in _CHECKS.items():
        try:
            await probe()
            checks[name] = "ok"
        except Exception:
            checks[name] = "down"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return ok({"status": status, "checks": checks})

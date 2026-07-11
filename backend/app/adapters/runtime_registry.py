"""SSH 类运行时的适配器注册表(T1.7/T1.8 复用点)。

把「一个 runtime 用哪个适配器类、动作目标取 runtime_ref 的哪个键」这一映射
集中一处,供 LifecycleService(生命周期动作)与 StatusCollector(状态采集)共享,
避免两处各维护一份易漂移的 if/else。

k8s 不经 Executor(走 client),不在此表;其余未列 runtime 视为暂不支持。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.docker_runtime import DockerRuntime
from app.adapters.systemd_runtime import SystemdRuntime
from app.models.service import Runtime


@dataclass(frozen=True)
class RuntimeAdapterSpec:
    """把一个 runtime 绑定到它的适配器类与 runtime_ref 目标键。

    适配器约定:构造只接收一个 Executor,并暴露 start/stop/restart/delete/status
    同名方法,目标参数为 runtime_ref[ref_key]。
    """

    adapter_cls: type
    ref_key: str


# 支持经 SSH/Executor 执行的裸机类 runtime。
SSH_RUNTIMES: dict[Runtime, RuntimeAdapterSpec] = {
    Runtime.SYSTEMD: RuntimeAdapterSpec(SystemdRuntime, "unit_name"),
    Runtime.DOCKER: RuntimeAdapterSpec(DockerRuntime, "container_name"),
}

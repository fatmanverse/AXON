"""统一执行器接口与工厂路由(T1.3,设计 §5.1)。

上层业务只依赖抽象 Executor 接口,底层 SSH / Agent 差异由具体实现屏蔽;
ExecutorFactory 按 server.access_mode 路由到对应实现。从 SSH 平滑升级到
Agent 时,上层与 UI 一行不改(§5.1「统一模型对上,多态执行对下」)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from app.models.server import AccessMode


@dataclass(frozen=True)
class CommandResult:
    """命令执行结果。succeeded 由 exit_code 判定,调用方据此决定后续。"""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class DeploySpec:
    """部署规格:制品地址 + 运行环境变量 + 各 runtime 的发布目标(二期自建部署)。

    artifact 是制品的通用寻址(镜像坐标或 tar 包路径);其余字段按 runtime 择用:
    - docker/k8s 用 image(含 tag/digest 的镜像坐标)。
    - docker 用 container_name / ports;systemd 用 unit_name / deploy_path;
      k8s 用 workload / namespace。
    不同 runtime 只读自己需要的字段,互不干扰(未用到的留 None)。
    """

    artifact: str
    env: dict[str, str] = field(default_factory=dict)
    image: str | None = None
    container_name: str | None = None
    unit_name: str | None = None
    deploy_path: str | None = None
    workload: str | None = None
    namespace: str | None = None
    ports: list[str] | None = None
    replicas: int | None = None


@dataclass(frozen=True)
class ServiceStatus:
    """服务观测状态。detail 承载 runtime 原始描述,供上层展示/诊断。"""

    name: str
    running: bool
    detail: str = ""


class Executor(ABC):
    """统一执行器接口(§5.1)。

    四个动作覆盖运行态操作:执行命令、部署、下发配置、拉服务状态。
    所有方法异步,适配 SSH/gRPC 等 I/O 密集场景。
    """

    @abstractmethod
    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult: ...

    @abstractmethod
    async def deploy(self, spec: DeploySpec) -> CommandResult: ...

    @abstractmethod
    async def update_config(self, path: str, content: str) -> CommandResult: ...

    @abstractmethod
    async def get_service_status(self, service_ref: str) -> ServiceStatus: ...


ExecutorBuilder = Callable[[], Executor]


class ExecutorFactory:
    """按 access_mode 注册与构造执行器。

    用注册表而非硬编码 if/else:新增接入模式(如未来的 Agent)只需注册,
    不改工厂本身;测试可注册 fake executor 隔离真实 SSH/Agent。
    """

    def __init__(self) -> None:
        self._builders: dict[AccessMode, ExecutorBuilder] = {}

    def register(self, mode: AccessMode, builder: ExecutorBuilder) -> None:
        self._builders[mode] = builder

    def create(self, mode: AccessMode) -> Executor:
        builder = self._builders.get(mode)
        if builder is None:
            raise ValueError(f"未注册的接入模式: {mode}")
        return builder()

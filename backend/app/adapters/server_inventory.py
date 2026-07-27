"""服务器现状探测(读取"这台机器上到底跑着什么")。

与既有 StatusCollector 的区别:StatusCollector 是"我知道该跑 X,去看 X 还活着吗"
(按已登记 service 探单点);本模块是"这台机器上实际有哪些容器/服务/监听端口/资源
水位",做全量发现,不依赖控制面是否登记过——供纳管一台已有机器后即刻看清现状。

设计:
- 复用统一 Executor.exec 通道(SSH 已封装建连/认证/超时),不新建传输层。
- 四个探测项各自独立:docker 容器 / systemd 服务 / 监听端口 / 资源水位。任一项
  命令失败(未装 docker、无权限、命令缺失)只把该项标记为 unavailable 并附错误,
  不影响其余项——保证"部分可见"好过"整体 500"。
- 所有解析都是模块级纯函数,吃命令 stdout 吐结构化数据,便于独立单测(不触 SSH)。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from app.adapters.executor import Executor
from app.core.logging import get_logger

log = get_logger("server_inventory")

# docker ps 输出每行一个 JSON 对象(--format '{{json .}}'),取这些字段。
_DOCKER_PS_CMD = "docker ps --all --no-trunc --format '{{json .}}'"
# systemctl 以 JSON 数组输出运行中的 service 单元。
_SYSTEMD_CMD = "systemctl list-units --type=service --state=running --output=json --no-pager"
# 监听端口:-H 去表头,-t/-u TCP+UDP,-l 仅监听,-n 数字化,-p 带进程(可能需 root)。
_PORTS_CMD = "ss -H -tulnp"
# 资源水位:一条命令内取内存 / 根分区磁盘 / 平均负载,用易解析的分隔标记分段。
_RESOURCE_CMD = (
    "echo '#MEM'; free -m; "
    "echo '#DISK'; df -PT -k /; "
    "echo '#LOAD'; cat /proc/loadavg"
)

# 单项探测命令超时(秒):发现命令都很快,给足冗余又不至于长挂。
_PROBE_TIMEOUT = 15.0


@dataclass(frozen=True)
class ContainerInfo:
    """一个 docker 容器的精简画像。"""

    name: str
    image: str
    status: str
    state: str
    ports: str


@dataclass(frozen=True)
class SystemdServiceInfo:
    """一个 systemd service 单元的精简画像。"""

    unit: str
    active: str
    sub: str
    description: str


@dataclass(frozen=True)
class ListenPortInfo:
    """一个监听端口。"""

    protocol: str
    address: str
    port: str
    process: str


@dataclass(frozen=True)
class ResourceSnapshot:
    """机器资源水位快照(取不到的字段为 None)。"""

    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    disk_total_kb: int | None = None
    disk_used_kb: int | None = None
    disk_mount: str | None = None
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None


@dataclass(frozen=True)
class InventorySection:
    """一个探测项的结果:available=False 时 items 为空、error 说明为何不可用。"""

    available: bool
    error: str | None = None


@dataclass(frozen=True)
class ServerInventory:
    """一台服务器的现状快照。各分区独立可用性,互不拖累。"""

    containers: list[ContainerInfo] = field(default_factory=list)
    containers_section: InventorySection = field(default_factory=lambda: InventorySection(True))
    services: list[SystemdServiceInfo] = field(default_factory=list)
    services_section: InventorySection = field(default_factory=lambda: InventorySection(True))
    ports: list[ListenPortInfo] = field(default_factory=list)
    ports_section: InventorySection = field(default_factory=lambda: InventorySection(True))
    resource: ResourceSnapshot | None = None
    resource_section: InventorySection = field(default_factory=lambda: InventorySection(True))


class InventoryProbe:
    """经注入 Executor 并发跑发现命令,汇总成 ServerInventory。

    与传输层解耦:SSH 机器注入 SSHExecutor;命令都是只读发现命令,不改目标机状态。
    """

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def probe(self) -> ServerInventory:
        """并发探测四项;各项独立降级,任一失败不影响其余。"""
        containers, services, ports, resource = await asyncio.gather(
            self._probe_containers(),
            self._probe_services(),
            self._probe_ports(),
            self._probe_resource(),
        )
        return ServerInventory(
            containers=containers[0],
            containers_section=containers[1],
            services=services[0],
            services_section=services[1],
            ports=ports[0],
            ports_section=ports[1],
            resource=resource[0],
            resource_section=resource[1],
        )

    async def _probe_containers(self) -> tuple[list[ContainerInfo], InventorySection]:
        section, stdout = await self._run(_DOCKER_PS_CMD, "docker")
        if not section.available:
            return [], section
        return parse_docker_ps(stdout), section

    async def _probe_services(self) -> tuple[list[SystemdServiceInfo], InventorySection]:
        section, stdout = await self._run(_SYSTEMD_CMD, "systemctl")
        if not section.available:
            return [], section
        return parse_systemd_units(stdout), section

    async def _probe_ports(self) -> tuple[list[ListenPortInfo], InventorySection]:
        section, stdout = await self._run(_PORTS_CMD, "ss")
        if not section.available:
            return [], section
        return parse_listen_ports(stdout), section

    async def _probe_resource(self) -> tuple[ResourceSnapshot | None, InventorySection]:
        section, stdout = await self._run(_RESOURCE_CMD, "resource")
        if not section.available:
            return None, section
        return parse_resource(stdout), section

    async def _run(self, command: str, probe_name: str) -> tuple[InventorySection, str]:
        """跑一条发现命令。命令层异常或非 0 退出都降级为不可用,不上抛。"""
        try:
            result = await self._executor.exec(command, timeout=_PROBE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — 单项探测失败降级,不拖垮整体
            log.info("inventory_probe_failed", probe=probe_name, error_type=type(exc).__name__)
            return InventorySection(available=False, error=_short_error(str(exc))), ""
        if not result.succeeded:
            detail = result.stderr.strip() or result.stdout.strip()
            return InventorySection(available=False, error=_short_error(detail)), ""
        return InventorySection(available=True), result.stdout


# ── 模块级纯解析函数(独立可单测,不触 SSH) ────────────────────────────────


def parse_docker_ps(stdout: str) -> list[ContainerInfo]:
    """解析 `docker ps --format '{{json .}}'`:每行一个 JSON 对象。

    坏行(非 JSON)跳过而非抛错——发现结果尽力而为,不因一行畸形丢掉整台机器的可见性。
    """
    containers: list[ContainerInfo] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(
            ContainerInfo(
                name=str(obj.get("Names", "")),
                image=str(obj.get("Image", "")),
                status=str(obj.get("Status", "")),
                state=str(obj.get("State", "")),
                ports=str(obj.get("Ports", "")),
            )
        )
    return containers


def parse_systemd_units(stdout: str) -> list[SystemdServiceInfo]:
    """解析 `systemctl list-units --output=json`:一个 JSON 数组。

    非 JSON(旧 systemd 无 --output=json)时返回空——调用方据 section.available 判定,
    这里防御性地对畸形输出返回空而非抛。
    """
    stdout = stdout.strip()
    if not stdout:
        return []
    try:
        arr = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(arr, list):
        return []
    services: list[SystemdServiceInfo] = []
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        services.append(
            SystemdServiceInfo(
                unit=str(obj.get("unit", "")),
                active=str(obj.get("active", "")),
                sub=str(obj.get("sub", "")),
                description=str(obj.get("description", "")),
            )
        )
    return services


def parse_listen_ports(stdout: str) -> list[ListenPortInfo]:
    """解析 `ss -H -tulnp` 输出行。

    典型行:tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))
    列不定长,取 Netid / Local Address:Port / users 段。解析不了的行跳过。
    """
    ports: list[ListenPortInfo] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        protocol = parts[0]
        local = parts[4]
        address, _, port = local.rpartition(":")
        if not port:
            continue
        process = _extract_ss_process(line)
        ports.append(
            ListenPortInfo(
                protocol=protocol,
                address=address or "*",
                port=port,
                process=process,
            )
        )
    return ports


def _extract_ss_process(line: str) -> str:
    """从 ss 行的 users:(("name",pid=..,fd=..)) 段提取首个进程名;取不到返回空串。"""
    marker = 'users:(("'
    idx = line.find(marker)
    if idx == -1:
        return ""
    rest = line[idx + len(marker) :]
    end = rest.find('"')
    return rest[:end] if end != -1 else ""


def parse_resource(stdout: str) -> ResourceSnapshot:
    """解析拼接的 free/df/loadavg 输出(用 #MEM/#DISK/#LOAD 分段标记切分)。"""
    section = ""
    mem_total = mem_used = None
    disk_total = disk_used = None
    disk_mount = None
    load1 = load5 = load15 = None

    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#MEM"):
            section = "mem"
            continue
        if line.startswith("#DISK"):
            section = "disk"
            continue
        if line.startswith("#LOAD"):
            section = "load"
            continue

        if section == "mem" and line.lower().startswith("mem:"):
            parts = line.split()
            mem_total = _to_int(parts[1]) if len(parts) > 1 else None
            mem_used = _to_int(parts[2]) if len(parts) > 2 else None
        elif section == "disk":
            parts = line.split()
            # df -PT -k / 的数据行:Filesystem Type 1024-blocks Used Available Capacity Mounted
            if len(parts) >= 7 and parts[2].isdigit():
                disk_total = _to_int(parts[2])
                disk_used = _to_int(parts[3])
                disk_mount = parts[6]
        elif section == "load":
            parts = line.split()
            if len(parts) >= 3:
                load1 = _to_float(parts[0])
                load5 = _to_float(parts[1])
                load15 = _to_float(parts[2])

    return ResourceSnapshot(
        mem_total_mb=mem_total,
        mem_used_mb=mem_used,
        disk_total_kb=disk_total,
        disk_used_kb=disk_used,
        disk_mount=disk_mount,
        load1=load1,
        load5=load5,
        load15=load15,
    )


def _to_int(text: str) -> int | None:
    try:
        return int(text)
    except (ValueError, TypeError):
        return None


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _short_error(text: str) -> str:
    """探测错误摘要:截断到合理长度,避免把整段 stderr 塞进响应。"""
    text = text.strip()
    return text[:200] if text else "命令执行失败"

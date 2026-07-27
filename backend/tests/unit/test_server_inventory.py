"""服务器现状探测单测:纯解析函数 + 探测器分项降级。

解析函数直接吃命令 stdout,不触 SSH;探测器注入 fake executor 验证:
- 四项并发探测汇总正确;
- 单项命令失败(异常/非 0 退出)只把该项标 unavailable,不影响其余项。
"""

from __future__ import annotations

import pytest

from app.adapters.executor import CommandResult
from app.adapters.server_inventory import (
    InventoryProbe,
    parse_docker_ps,
    parse_listen_ports,
    parse_resource,
    parse_systemd_units,
)

# ── 解析函数 ────────────────────────────────────────────────────────────────


def test_parse_docker_ps_reads_each_json_line():
    stdout = (
        '{"Names":"web","Image":"nginx:1.25","Status":"Up 2 hours",'
        '"State":"running","Ports":"0.0.0.0:80->80/tcp"}\n'
        '{"Names":"db","Image":"postgres:16","Status":"Exited (0) 1 day ago",'
        '"State":"exited","Ports":""}\n'
    )
    containers = parse_docker_ps(stdout)
    assert [c.name for c in containers] == ["web", "db"]
    assert containers[0].image == "nginx:1.25"
    assert containers[0].state == "running"
    assert containers[1].status.startswith("Exited")


def test_parse_docker_ps_skips_bad_lines():
    stdout = '{"Names":"ok","Image":"a","Status":"s","State":"running","Ports":""}\nNOT JSON\n\n'
    containers = parse_docker_ps(stdout)
    assert len(containers) == 1
    assert containers[0].name == "ok"


def test_parse_systemd_units_reads_json_array():
    stdout = (
        '[{"unit":"nginx.service","active":"active","sub":"running",'
        '"description":"A high performance web server"},'
        '{"unit":"ssh.service","active":"active","sub":"running","description":"OpenBSD SSH"}]'
    )
    services = parse_systemd_units(stdout)
    assert [s.unit for s in services] == ["nginx.service", "ssh.service"]
    assert services[0].sub == "running"


def test_parse_systemd_units_tolerates_non_json():
    # 旧 systemd 不支持 --output=json,吐表格文本:防御性返回空,不抛。
    assert parse_systemd_units("UNIT LOAD ACTIVE SUB DESCRIPTION\n") == []
    assert parse_systemd_units("") == []


def test_parse_listen_ports_extracts_port_and_process():
    stdout = (
        'tcp   LISTEN 0 128  0.0.0.0:22    0.0.0.0:*  users:(("sshd",pid=1,fd=3))\n'
        'tcp   LISTEN 0 128  [::]:443      [::]:*     users:(("nginx",pid=20,fd=6))\n'
        'udp   UNCONN 0 0    0.0.0.0:68    0.0.0.0:*\n'
    )
    ports = parse_listen_ports(stdout)
    assert len(ports) == 3
    assert ports[0].port == "22"
    assert ports[0].process == "sshd"
    assert ports[1].port == "443"
    assert ports[1].process == "nginx"
    # 无 users 段的行进程为空,但仍解析出端口
    assert ports[2].port == "68"
    assert ports[2].process == ""


def test_parse_listen_ports_skips_short_lines():
    assert parse_listen_ports("tcp LISTEN\n\n") == []


def test_parse_resource_reads_mem_disk_load():
    stdout = (
        "#MEM\n"
        "              total        used        free\n"
        "Mem:           7820        3110        1200\n"
        "Swap:          2047           0        2047\n"
        "#DISK\n"
        "Filesystem     Type 1024-blocks     Used Available Capacity Mounted on\n"
        "/dev/vda1      ext4    41152000 20000000  19000000      52% /\n"
        "#LOAD\n"
        "0.15 0.22 0.30 1/234 5678\n"
    )
    snap = parse_resource(stdout)
    assert snap.mem_total_mb == 7820
    assert snap.mem_used_mb == 3110
    assert snap.disk_total_kb == 41152000
    assert snap.disk_used_kb == 20000000
    assert snap.disk_mount == "/"
    assert snap.load1 == 0.15
    assert snap.load5 == 0.22
    assert snap.load15 == 0.30


def test_parse_resource_missing_sections_yield_none():
    snap = parse_resource("#LOAD\n1.0 2.0 3.0 1/1 1\n")
    assert snap.mem_total_mb is None
    assert snap.disk_total_kb is None
    assert snap.load1 == 1.0


# ── 探测器分项降级 ──────────────────────────────────────────────────────────


class _ScriptedExecutor:
    """按命令关键字返回预设结果或抛异常的 fake executor。"""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        for key, value in self._responses.items():
            if key in command:
                if isinstance(value, Exception):
                    raise value
                return value  # type: ignore[return-value]
        return CommandResult(exit_code=127, stdout="", stderr="not found")

    async def deploy(self, spec):  # pragma: no cover - 未用到
        raise NotImplementedError

    async def update_config(self, path, content):  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_probe_aggregates_all_sections():
    executor = _ScriptedExecutor(
        {
            "docker ps": CommandResult(
                0,
                '{"Names":"web","Image":"nginx","Status":"Up","State":"running","Ports":""}',
                "",
            ),
            "list-units": CommandResult(
                0, '[{"unit":"ssh.service","active":"active","sub":"running","description":""}]', ""
            ),
            "ss -H": CommandResult(
                0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1))', ""
            ),
            "#MEM": CommandResult(0, "#MEM\nMem: 1000 400 600\n#LOAD\n0.1 0.2 0.3 1/1 1\n", ""),
        }
    )
    inv = await InventoryProbe(executor).probe()
    assert inv.containers_section.available
    assert inv.containers[0].name == "web"
    assert inv.services[0].unit == "ssh.service"
    assert inv.ports[0].port == "22"
    assert inv.resource is not None
    assert inv.resource.mem_total_mb == 1000


@pytest.mark.asyncio
async def test_probe_degrades_missing_docker_only():
    # docker 未装(命令抛/非0),其余项仍可用:部分可见好过整体失败。
    executor = _ScriptedExecutor(
        {
            "docker ps": CommandResult(127, "", "docker: command not found"),
            "list-units": CommandResult(0, "[]", ""),
            "ss -H": CommandResult(0, "", ""),
            "#MEM": CommandResult(0, "#MEM\nMem: 1 1 0\n", ""),
        }
    )
    inv = await InventoryProbe(executor).probe()
    assert inv.containers_section.available is False
    assert "not found" in (inv.containers_section.error or "")
    assert inv.containers == []
    # 其余项不受影响
    assert inv.services_section.available
    assert inv.ports_section.available
    assert inv.resource_section.available


@pytest.mark.asyncio
async def test_probe_degrades_on_exception():
    executor = _ScriptedExecutor(
        {
            "docker ps": CommandResult(0, "", ""),
            "list-units": RuntimeError("ssh dropped"),
            "ss -H": CommandResult(0, "", ""),
            "#MEM": CommandResult(0, "", ""),
        }
    )
    inv = await InventoryProbe(executor).probe()
    assert inv.services_section.available is False
    assert inv.containers_section.available

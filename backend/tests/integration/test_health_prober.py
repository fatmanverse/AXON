"""生产 HealthProber 验收(T3.8,§11.1)。

HealthChecker 的探测执行端(prober)之前只有测试 fake,生产从未有真实实现 →
即使注入 checker 也无法真探测。本 prober 支持两类探测:
- http:  发 GET,状态码命中 expect_status(默认 200)即通过。
- command: 经注入的 executor 跑命令,exit 命中 expect_exit(默认 0)即通过。

用 fake http client / fake executor,不触真实网络与子进程。
"""

from __future__ import annotations

from app.adapters.executor import CommandResult
from app.services.health_prober import DefaultHealthProber


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status_code = status


class _FakeHttp:
    def __init__(self, status: int) -> None:
        self._status = status
        self.requested: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method: str, url: str, **kwargs):
        self.requested.append(url)
        return _FakeResp(self._status)


class _FakeExecutor:
    def __init__(self, exit_code: int) -> None:
        self._exit = exit_code
        self.ran: list[str] = []

    async def exec(self, command: str, *, timeout=None) -> CommandResult:
        self.ran.append(command)
        return CommandResult(exit_code=self._exit, stdout="out", stderr="err")


async def test_http_probe_pass_on_expected_status():
    http = _FakeHttp(200)
    prober = DefaultHealthProber(http_factory=lambda: http)
    ok, detail = await prober.probe({"type": "http", "url": "http://x/health"})
    assert ok
    assert http.requested == ["http://x/health"]


async def test_http_probe_fail_on_wrong_status():
    prober = DefaultHealthProber(http_factory=lambda: _FakeHttp(503))
    ok, _ = await prober.probe({"type": "http", "url": "http://x", "expect_status": 200})
    assert not ok


async def test_command_probe_pass_on_expected_exit():
    ex = _FakeExecutor(0)
    prober = DefaultHealthProber(executor_factory=lambda: ex)
    ok, _ = await prober.probe({"type": "command", "command": "systemctl is-active billing"})
    assert ok
    assert ex.ran == ["systemctl is-active billing"]


async def test_command_probe_fail_on_nonzero_exit():
    prober = DefaultHealthProber(executor_factory=lambda: _FakeExecutor(3))
    ok, _ = await prober.probe({"type": "command", "command": "false"})
    assert not ok


async def test_unknown_type_fails_safe():
    prober = DefaultHealthProber()
    ok, detail = await prober.probe({"type": "tcp"})
    assert not ok
    assert "tcp" in detail

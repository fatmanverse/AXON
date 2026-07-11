"""T3.9 发布后健康检查(§11.1)。

HealthChecker 是可注入探测器的编排核心:按 health_check 配置(HTTP/命令探测)
探测,支持重试;全部尝试失败才判失败。探测的实际执行(HTTP 请求/命令执行)
通过注入的 prober 解耦,便于单测,不触真实网络/子进程。

判定语义:
- 无 health_check 配置 → 视为健康(不阻断,MVP 宽松)。
- HTTP:实际状态码 == expect_status(默认 200)为通过。
- 命令:退出码 == expect_exit(默认 0)为通过。
- retries 次尝试内任一次通过即健康;全失败才不健康。
"""


from app.services.health_checker import HealthChecker


class _FakeProber:
    """按预置脚本返回探测结果:results 是每次调用要返回的 (ok, detail) 列表。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    async def probe(self, config):
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return (False, "no more results")


async def test_no_config_is_healthy():
    checker = HealthChecker(prober=_FakeProber([]))
    result = await checker.check(None)
    assert result.healthy is True
    assert result.attempts == 0


async def test_first_attempt_success():
    prober = _FakeProber([(True, "200 OK")])
    checker = HealthChecker(prober=prober)
    result = await checker.check({"type": "http", "url": "http://x/health"})
    assert result.healthy is True
    assert result.attempts == 1
    assert prober.calls == 1


async def test_retries_then_success():
    # 前两次失败,第三次成功;retries=3 应最终健康
    prober = _FakeProber([(False, "500"), (False, "timeout"), (True, "200")])
    checker = HealthChecker(prober=prober)
    result = await checker.check(
        {"type": "http", "url": "http://x/health", "retries": 3, "interval_sec": 0}
    )
    assert result.healthy is True
    assert result.attempts == 3


async def test_all_attempts_fail():
    prober = _FakeProber([(False, "500"), (False, "500")])
    checker = HealthChecker(prober=prober)
    result = await checker.check(
        {"type": "http", "url": "http://x/health", "retries": 2, "interval_sec": 0}
    )
    assert result.healthy is False
    assert result.attempts == 2
    assert "500" in result.detail


async def test_command_probe_type():
    prober = _FakeProber([(True, "exit 0")])
    checker = HealthChecker(prober=prober)
    result = await checker.check(
        {"type": "command", "command": "systemctl is-active billing", "expect_exit": 0}
    )
    assert result.healthy is True
    assert result.attempts == 1


async def test_default_retries_is_one():
    # 不配 retries 时默认尝试一次
    prober = _FakeProber([(False, "down")])
    checker = HealthChecker(prober=prober)
    result = await checker.check({"type": "http", "url": "http://x/health", "interval_sec": 0})
    assert result.healthy is False
    assert result.attempts == 1

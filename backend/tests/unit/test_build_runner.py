"""BuildRunner 构建步骤编排(构建能力一期,方案 A 本地构建)。

用可编程 FakeExecutor 记录命令序列并按规则回应,验证:
- generic 形态:clone → rev-parse 回填 git_sha → 测试 → build → tar 打包,
  产出 BuildOutcome(uri 指向 tar 包)。
- docker 形态:clone → rev-parse → 测试 → build → docker build → docker push
  → inspect 取 digest。
- test_command 为空则跳过测试步骤。
- 任一步非 0 抛 AppError(build_step_failed, 502),错误信息含 stderr 摘要。
- repo_url / git_ref 等外部输入经 shlex 转义,不能裸拼进命令。

单测不触碰真实 git/docker。
"""

import pytest

from app.adapters.build_runner import BuildRunner, BuildSpec
from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.core.errors import AppError

_SHA = "a" * 40


class FakeExecutor(Executor):
    """记录命令;按子串规则返回结果,默认成功空输出。"""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.ran: list[str] = []
        self._fail_on = fail_on

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        if self._fail_on and self._fail_on in command:
            return CommandResult(exit_code=1, stdout="", stderr=f"step failed: {self._fail_on}")
        if "rev-parse" in command:
            return CommandResult(exit_code=0, stdout=f"{_SHA}\n", stderr="")
        if "stat -" in command or "wc -c" in command:
            return CommandResult(exit_code=0, stdout="2048\n", stderr="")
        if "docker inspect" in command:
            return CommandResult(exit_code=0, stdout="sha256:" + "b" * 64 + "\n", stderr="")
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


def _generic_spec(**overrides) -> BuildSpec:
    defaults = dict(
        repo_url="https://git.example.com/team/app.git",
        git_ref="main",
        workspace="/var/lib/axon/builds/b1",
        test_command="make test",
        build_command="make build",
        artifact_type="generic",
        output_path="dist",
        artifact_path="/var/lib/axon/artifacts/app-1.0.0.tar.gz",
    )
    defaults.update(overrides)
    return BuildSpec(**defaults)


def _docker_spec(**overrides) -> BuildSpec:
    defaults = dict(
        repo_url="https://git.example.com/team/app.git",
        git_ref="main",
        workspace="/var/lib/axon/builds/b2",
        test_command="make test",
        build_command="make build",
        artifact_type="docker",
        image_ref="registry.example.com/team/app:1.0.0",
        dockerfile="Dockerfile",
    )
    defaults.update(overrides)
    return BuildSpec(**defaults)


async def test_generic_build_runs_steps_in_order_and_returns_outcome():
    executor = FakeExecutor()
    runner = BuildRunner(executor)

    outcome = await runner.run(_generic_spec())

    joined = "\n".join(executor.ran)
    assert "git clone" in joined
    assert "rev-parse" in joined
    assert "make test" in joined
    assert "make build" in joined
    assert "tar czf" in joined
    # clone 必须先于测试与构建
    assert joined.index("git clone") < joined.index("make test") < joined.index("make build")
    assert outcome.git_sha == _SHA
    assert outcome.artifact_uri == "/var/lib/axon/artifacts/app-1.0.0.tar.gz"
    assert outcome.size_bytes == 2048


async def test_docker_build_pushes_and_reads_digest():
    executor = FakeExecutor()
    runner = BuildRunner(executor)

    outcome = await runner.run(_docker_spec())

    joined = "\n".join(executor.ran)
    assert "docker build" in joined
    assert "docker push" in joined
    assert "docker inspect" in joined
    assert outcome.artifact_uri == "registry.example.com/team/app:1.0.0"
    assert outcome.digest == "sha256:" + "b" * 64


async def test_empty_test_command_skips_test_step():
    executor = FakeExecutor()
    runner = BuildRunner(executor)

    await runner.run(_generic_spec(test_command=None))

    joined = "\n".join(executor.ran)
    assert "make test" not in joined
    assert "make build" in joined


async def test_failing_step_raises_build_step_failed():
    executor = FakeExecutor(fail_on="make build")
    runner = BuildRunner(executor)

    with pytest.raises(AppError) as excinfo:
        await runner.run(_generic_spec())

    assert excinfo.value.code == "build_step_failed"
    assert excinfo.value.status_code == 502
    assert "step failed" in excinfo.value.message


async def test_repo_url_and_ref_are_shell_escaped():
    executor = FakeExecutor()
    runner = BuildRunner(executor)
    spec = _generic_spec(
        repo_url="https://git.example.com/x.git; rm -rf /",
        git_ref="main; touch /pwn",
    )

    await runner.run(spec)

    clone_cmd = next(c for c in executor.ran if "git clone" in c)
    # 恶意输入必须被整体引用,不能裸拼
    assert "'https://git.example.com/x.git; rm -rf /'" in clone_cmd
    assert "'main; touch /pwn'" in clone_cmd

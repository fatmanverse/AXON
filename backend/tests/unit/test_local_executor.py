"""LocalExecutor 本地子进程执行器(构建能力一期,方案 A「1 号构建节点」)。

控制面主机自身执行构建命令(git clone/测试/build)。用真实子进程验证:
- 成功命令返回 exit_code=0 与 stdout。
- 失败命令返回非 0 与 stderr,不抛(与 SSHExecutor 语义一致:业务失败靠
  CommandResult 表达,基础设施故障才抛 AppError)。
- 超时抛 AppError(local_exec_timeout, 504),对齐 SSHExecutor 的 ssh_timeout。
- workdir 生效:命令在指定目录下执行。
- deploy/update_config/get_service_status 非构建职责,抛 NotImplementedError。
"""

import pytest

from app.adapters.executor import DeploySpec
from app.adapters.local_executor import LocalExecutor
from app.core.errors import AppError


async def test_exec_success_returns_stdout(tmp_path):
    executor = LocalExecutor(workdir=tmp_path)

    result = await executor.exec("printf hello-local")

    assert result.succeeded
    assert result.exit_code == 0
    assert result.stdout == "hello-local"


async def test_exec_failure_returns_nonzero_and_stderr(tmp_path):
    executor = LocalExecutor(workdir=tmp_path)

    result = await executor.exec("echo boom >&2; exit 3")

    assert not result.succeeded
    assert result.exit_code == 3
    assert "boom" in result.stderr


async def test_exec_runs_in_workdir(tmp_path):
    (tmp_path / "marker.txt").write_text("present")
    executor = LocalExecutor(workdir=tmp_path)

    result = await executor.exec("cat marker.txt")

    assert result.succeeded
    assert result.stdout == "present"


async def test_exec_timeout_raises_app_error(tmp_path):
    executor = LocalExecutor(workdir=tmp_path)

    with pytest.raises(AppError) as excinfo:
        await executor.exec("sleep 5", timeout=0.2)

    assert excinfo.value.code == "local_exec_timeout"
    assert excinfo.value.status_code == 504


async def test_non_build_actions_are_not_implemented(tmp_path):
    executor = LocalExecutor(workdir=tmp_path)

    with pytest.raises(NotImplementedError):
        await executor.deploy(DeploySpec(artifact="x"))
    with pytest.raises(NotImplementedError):
        await executor.update_config("/etc/x", "y")
    with pytest.raises(NotImplementedError):
        await executor.get_service_status("x")

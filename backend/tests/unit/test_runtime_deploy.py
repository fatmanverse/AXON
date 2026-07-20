"""三 runtime 的 deploy(发布制品)方法单测(二期:控制面自建部署)。

与生命周期动作并列的新语义——把制品真正送上目标机运行:
- DockerRuntime.deploy:pull 镜像 → 幂等清旧容器 → run(带 env/端口/重启策略)。
- SystemdRuntime.deploy:解包 tar 制品到部署目录 → daemon-reload → restart。
- K8sRuntime.deploy:patch deployment 的容器镜像(set-image 触发滚动更新)。

用 fake executor/client 记录命令与 patch body,验证命令序列、shlex 转义、
失败抛 AppError。单测不触碰真实 docker/systemd/集群。
"""

from types import SimpleNamespace

import pytest

from app.adapters.docker_runtime import DockerRuntime
from app.adapters.executor import CommandResult, DeploySpec, Executor, ServiceStatus
from app.adapters.k8s_runtime import K8sRuntime
from app.adapters.systemd_runtime import SystemdRuntime
from app.core.errors import AppError


class FakeExecutor(Executor):
    """记录命令;可配置在某子串命令上失败。"""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.ran: list[str] = []
        self._fail_on = fail_on

    async def exec(self, command: str, *, timeout: float | None = None) -> CommandResult:
        self.ran.append(command)
        if self._fail_on and self._fail_on in command:
            return CommandResult(exit_code=1, stdout="", stderr="boom")
        return CommandResult(exit_code=0, stdout="", stderr="")

    async def deploy(self, spec: DeploySpec) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def update_config(self, path: str, content: str) -> CommandResult:  # pragma: no cover
        raise NotImplementedError

    async def get_service_status(self, service_ref: str) -> ServiceStatus:  # pragma: no cover
        raise NotImplementedError


# ── DockerRuntime.deploy ───────────────────────────────────────────


async def test_docker_deploy_pulls_removes_old_and_runs():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)
    spec = DeploySpec(
        artifact="registry.example.com/team/app:1.0.0",
        image="registry.example.com/team/app:1.0.0",
        container_name="billing",
        env={"ENV": "prod"},
        ports=["8080:80"],
    )

    await runtime.deploy(spec)

    joined = "\n".join(executor.ran)
    assert "docker pull registry.example.com/team/app:1.0.0" in joined
    assert "docker rm -f billing" in joined
    assert "docker run -d --name billing" in joined
    assert "--restart unless-stopped" in joined
    assert "-e ENV=prod" in joined
    assert "-p 8080:80" in joined
    # pull 必须先于 run
    assert joined.index("docker pull") < joined.index("docker run")


async def test_docker_deploy_shell_escapes_inputs():
    executor = FakeExecutor()
    runtime = DockerRuntime(executor)
    spec = DeploySpec(
        artifact="x",
        image="img; rm -rf /",
        container_name="c$(whoami)",
        env={"K": "v; evil"},
    )

    await runtime.deploy(spec)

    joined = "\n".join(executor.ran)
    assert "'img; rm -rf /'" in joined
    assert "'c$(whoami)'" in joined
    assert "'K=v; evil'" in joined or "K='v; evil'" in joined


async def test_docker_deploy_raises_on_pull_failure():
    executor = FakeExecutor(fail_on="docker pull")
    runtime = DockerRuntime(executor)
    spec = DeploySpec(artifact="img", image="img", container_name="c")

    with pytest.raises(AppError) as excinfo:
        await runtime.deploy(spec)

    assert excinfo.value.code == "docker_action_failed"


# ── SystemdRuntime.deploy ──────────────────────────────────────────


async def test_systemd_deploy_unpacks_and_restarts():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)
    spec = DeploySpec(
        artifact="/var/lib/axon/artifacts/app-1.0.0.tar.gz",
        unit_name="billing.service",
        deploy_path="/opt/billing",
    )

    await runtime.deploy(spec)

    joined = "\n".join(executor.ran)
    assert "tar" in joined
    assert "/var/lib/axon/artifacts/app-1.0.0.tar.gz" in joined
    assert "/opt/billing" in joined
    assert "systemctl daemon-reload" in joined
    assert "systemctl restart billing.service" in joined
    # 解包必须先于重启
    assert joined.index("tar") < joined.index("systemctl restart")


async def test_systemd_deploy_shell_escapes_inputs():
    executor = FakeExecutor()
    runtime = SystemdRuntime(executor)
    spec = DeploySpec(
        artifact="/tmp/a.tar.gz; rm -rf /",
        unit_name="evil.service",
        deploy_path="/opt/x; touch /pwn",
    )

    await runtime.deploy(spec)

    joined = "\n".join(executor.ran)
    assert "'/tmp/a.tar.gz; rm -rf /'" in joined
    assert "'/opt/x; touch /pwn'" in joined


async def test_systemd_deploy_raises_on_unpack_failure():
    executor = FakeExecutor(fail_on="tar")
    runtime = SystemdRuntime(executor)
    spec = DeploySpec(artifact="/tmp/a.tar.gz", unit_name="x.service", deploy_path="/opt/x")

    with pytest.raises(AppError) as excinfo:
        await runtime.deploy(spec)

    assert excinfo.value.code == "systemd_action_failed"


# ── K8sRuntime.deploy ──────────────────────────────────────────────


class FakeAppsV1Api:
    """记录 patch 调用的假 AppsV1Api。"""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.patched: list[dict] = []

    async def patch_namespaced_deployment(
        self, name: str, namespace: str, body: list[dict], **kwargs
    ):
        if self._fail:
            raise RuntimeError("patch rejected")
        self.patched.append({"name": name, "namespace": namespace, "body": body, "kwargs": kwargs})

    async def read_namespaced_deployment(self, name: str, namespace: str):  # pragma: no cover
        return SimpleNamespace(
            spec=SimpleNamespace(replicas=1),
            status=SimpleNamespace(ready_replicas=1),
        )

    async def patch_namespaced_deployment_scale(self, name, namespace, body):  # pragma: no cover
        raise NotImplementedError

    async def delete_namespaced_deployment(self, name, namespace):  # pragma: no cover
        raise NotImplementedError


async def test_k8s_deploy_sets_container_image():
    api = FakeAppsV1Api()
    runtime = K8sRuntime(api)
    spec = DeploySpec(
        artifact="registry.example.com/team/app:2.0.0",
        image="registry.example.com/team/app:2.0.0",
        workload="billing",
        namespace="billing-prod",
    )

    await runtime.deploy(spec)

    assert len(api.patched) == 1
    patch = api.patched[0]
    assert patch["name"] == "billing"
    assert patch["namespace"] == "billing-prod"
    assert patch["body"] == [
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/image",
            "value": "registry.example.com/team/app:2.0.0",
        }
    ]
    assert patch["kwargs"] == {"_content_type": "application/json-patch+json"}


async def test_k8s_deploy_raises_on_patch_failure():
    api = FakeAppsV1Api(fail=True)
    runtime = K8sRuntime(api)
    spec = DeploySpec(artifact="img", image="img", workload="billing", namespace="ns")

    with pytest.raises(AppError) as excinfo:
        await runtime.deploy(spec)

    assert excinfo.value.code == "k8s_action_failed"

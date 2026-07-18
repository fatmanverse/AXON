"""BuildRunner 构建步骤编排(构建能力一期,方案 A「本地构建」)。

把一次「clone → 测试 → build → 产出制品」拆成有序 shell 步骤,经注入的
Executor 逐步执行。与传输层解耦:本地构建注入 LocalExecutor,后续把构建派到
SSH 构建节点时注入 SSHExecutor,BuildRunner 一行不改(§方案 A 可扩展)。

制品两形态(按服务 build_config.artifact_type 选):
- generic:tar 打包 workspace 下的产物目录,uri 指向控制面本地制品文件。
- docker:docker build → push 到镜像库,uri 是镜像坐标,digest 取 inspect 结果。

安全:repo_url / git_ref / 各命令段等外部输入一律 shlex.quote 转义,杜绝命令
注入(先例:node_exporter._bootstrap_script)。任一步非 0 抛 AppError 携 stderr。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from app.adapters.executor import CommandResult, Executor
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("build_runner")


@dataclass(frozen=True)
class BuildSpec:
    """一次构建的输入规格。generic 与 docker 各用其形态字段。"""

    repo_url: str
    git_ref: str
    workspace: str
    build_command: str
    artifact_type: str  # "generic" | "docker"
    test_command: str | None = None
    # generic 形态:workspace 下待打包的产物目录 + 输出的 tar 包路径。
    output_path: str | None = None
    artifact_path: str | None = None
    # docker 形态:镜像坐标 + Dockerfile 路径(相对 workspace)。
    image_ref: str | None = None
    dockerfile: str = "Dockerfile"


@dataclass(frozen=True)
class BuildOutcome:
    """构建产物:关联键 git_sha + 制品寻址(uri/digest/size)。"""

    git_sha: str
    artifact_uri: str
    digest: str | None = None
    size_bytes: int | None = None


class BuildRunner:
    """经注入 Executor 顺序执行构建步骤,产出 BuildOutcome。"""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def run(self, spec: BuildSpec) -> BuildOutcome:
        ws = shlex.quote(spec.workspace)
        await self._step(
            f"rm -rf {ws} && git clone --depth 1 -b {shlex.quote(spec.git_ref)} "
            f"{shlex.quote(spec.repo_url)} {ws}"
        )
        git_sha = await self._resolve_git_sha(ws)

        if spec.test_command:
            await self._step(f"cd {ws} && {spec.test_command}")
        await self._step(f"cd {ws} && {spec.build_command}")

        if spec.artifact_type == "docker":
            return await self._package_docker(spec, ws, git_sha)
        return await self._package_generic(spec, ws, git_sha)

    async def _resolve_git_sha(self, ws: str) -> str:
        result = await self._step(f"git -C {ws} rev-parse HEAD")
        return result.stdout.strip()

    async def _package_generic(self, spec: BuildSpec, ws: str, git_sha: str) -> BuildOutcome:
        if not spec.artifact_path or not spec.output_path:
            raise AppError(
                "build_spec_invalid",
                "generic 构建需 output_path 与 artifact_path",
                status_code=400,
            )
        artifact = shlex.quote(spec.artifact_path)
        await self._step(
            f"tar czf {artifact} -C {ws} {shlex.quote(spec.output_path)}"
        )
        size = await self._read_size(spec.artifact_path)
        return BuildOutcome(git_sha=git_sha, artifact_uri=spec.artifact_path, size_bytes=size)

    async def _package_docker(self, spec: BuildSpec, ws: str, git_sha: str) -> BuildOutcome:
        if not spec.image_ref:
            raise AppError("build_spec_invalid", "docker 构建需 image_ref", status_code=400)
        image = shlex.quote(spec.image_ref)
        dockerfile = shlex.quote(spec.dockerfile)
        await self._step(f"docker build -t {image} -f {ws}/{dockerfile} {ws}")
        await self._step(f"docker push {image}")
        inspect = await self._step(
            f"docker inspect --format='{{{{index .RepoDigests 0}}}}' {image}"
        )
        digest = _extract_digest(inspect.stdout)
        return BuildOutcome(git_sha=git_sha, artifact_uri=spec.image_ref, digest=digest)

    async def _read_size(self, path: str) -> int | None:
        """取制品文件字节数;失败不致命(size 是展示信息),返回 None。"""
        result = await self._executor.exec(f"wc -c < {shlex.quote(path)}")
        if not result.succeeded:
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    async def _step(self, command: str) -> CommandResult:
        """执行一步;非 0 抛 build_step_failed(携 stderr 摘要)。"""
        result = await self._executor.exec(command)
        if not result.succeeded:
            detail = result.stderr.strip() or result.stdout.strip()
            log.warning("build_step_failed", exit_code=result.exit_code)
            raise AppError(
                "build_step_failed",
                f"构建步骤失败: {detail}",
                status_code=502,
            )
        return result


def _extract_digest(raw: str) -> str | None:
    """从 RepoDigests(name@sha256:...)提取 sha256 摘要;取不到返回 None。"""
    text = raw.strip().strip("'\"")
    if "@" in text:
        return text.split("@", 1)[1]
    if text.startswith("sha256:"):
        return text
    return None

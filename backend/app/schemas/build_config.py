"""服务构建配置(build_config)的结构化 schema。

此前 build_config 是自由 dict:字段名/必填只在触发构建的后台任务里由
BuildService._resolve 运行时才校验——用户在 UI 填错 key,建服务当场不报错,
要等"构建"跑后台任务才失败,反馈链路很长。此模型把校验前移到"编写即校验":
建服务 / 改配置提交时就按形态(generic|docker)校验必填项,当场给出明确错误。

形态与必填项(与 BuildService._build_spec 的真实读取一致,唯一真相源):
- 公共必填:repo_url、build_command。
- generic:output_path 必填(workspace 下待打包的产物目录)。
- docker:image_ref 必填(镜像坐标,注意是 image_ref 而非历史注释里的 image_name)。

可空项 test_command/git_ref/version/registry_id/required_labels/dockerfile 保持
与既有 BuildService 读取的默认语义一致(git_ref 缺省 main、dockerfile 缺省 Dockerfile)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ArtifactType = Literal["generic", "docker"]


class BuildConfigModel(BaseModel):
    """服务本地构建的结构化配置。提交即校验,拒绝残缺配置落库。

    extra="forbid":拒绝未知 key,避免用户拼错字段名(如 image_name)却静默通过、
    到构建时才因缺 image_ref 失败——错误在编写阶段就暴露。
    """

    model_config = {"extra": "forbid"}

    repo_url: str = Field(min_length=1, max_length=512, description="git clone 源地址")
    build_command: str = Field(min_length=1, max_length=2000, description="构建命令")
    artifact_type: ArtifactType = Field(default="generic", description="制品形态")
    git_ref: str = Field(default="main", max_length=255, description="分支/标签,触发时可覆盖")
    test_command: str | None = Field(
        default=None, max_length=2000, description="构建前测试命令,空则跳过"
    )
    version: str | None = Field(
        default=None, max_length=128, description="制品版本标签,触发时可覆盖"
    )
    registry_id: str | None = Field(
        default=None, max_length=32, description="指定制品库,缺省用默认库"
    )
    required_labels: dict[str, Any] = Field(default_factory=dict, description="构建节点选择标签")

    # generic 形态:workspace 下待打包的产物目录。
    output_path: str | None = Field(default=None, max_length=512, description="generic 产物目录")
    # docker 形态:镜像坐标 + Dockerfile 相对路径。
    image_ref: str | None = Field(default=None, max_length=512, description="docker 镜像坐标")
    dockerfile: str = Field(default="Dockerfile", max_length=512, description="Dockerfile 相对路径")

    @model_validator(mode="after")
    def validate_by_artifact_type(self) -> BuildConfigModel:
        """按形态校验专属必填项:generic 需 output_path,docker 需 image_ref。"""
        if self.artifact_type == "generic":
            if not self.output_path:
                raise ValueError("generic 制品必须提供 output_path(待打包的产物目录)")
        else:  # docker
            if not self.image_ref:
                raise ValueError("docker 制品必须提供 image_ref(镜像坐标)")
        return self

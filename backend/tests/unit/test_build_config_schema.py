"""BuildConfigModel 结构化校验单测(块二:构建配置编写即校验)。

验证形态联动必填、未知 key 拒绝、默认值,确保"编写即校验"在建服务/改配置时
就拦下残缺配置,而非拖到构建后台任务才失败。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.build_config import BuildConfigModel


def test_generic_config_requires_output_path():
    with pytest.raises(ValidationError) as exc:
        BuildConfigModel(
            repo_url="https://git.example.com/app.git",
            build_command="make build",
            artifact_type="generic",
        )
    assert "output_path" in str(exc.value)


def test_docker_config_requires_image_ref():
    with pytest.raises(ValidationError) as exc:
        BuildConfigModel(
            repo_url="https://git.example.com/app.git",
            build_command="docker build .",
            artifact_type="docker",
        )
    assert "image_ref" in str(exc.value)


def test_valid_generic_config_fills_defaults():
    cfg = BuildConfigModel(
        repo_url="https://git.example.com/app.git",
        build_command="make build",
        artifact_type="generic",
        output_path="dist",
    )
    # 默认值:git_ref=main、dockerfile=Dockerfile、artifact_type=generic
    assert cfg.git_ref == "main"
    assert cfg.dockerfile == "Dockerfile"
    assert cfg.test_command is None


def test_valid_docker_config():
    cfg = BuildConfigModel(
        repo_url="https://git.example.com/app.git",
        build_command="docker build -t app .",
        artifact_type="docker",
        image_ref="registry.example.com/app:1.0",
    )
    assert cfg.image_ref == "registry.example.com/app:1.0"


def test_artifact_type_defaults_to_generic():
    cfg = BuildConfigModel(
        repo_url="https://git.example.com/app.git",
        build_command="make build",
        output_path="dist",
    )
    assert cfg.artifact_type == "generic"


def test_missing_repo_url_rejected():
    with pytest.raises(ValidationError):
        BuildConfigModel(build_command="make build", output_path="dist")


def test_unknown_key_rejected():
    # 拼错字段名(如把 image_ref 写成 image_name)必须当场报错,而非静默通过。
    with pytest.raises(ValidationError) as exc:
        BuildConfigModel(
            repo_url="https://git.example.com/app.git",
            build_command="docker build .",
            artifact_type="docker",
            image_name="registry.example.com/app:1.0",  # 应为 image_ref
        )
    assert "image_name" in str(exc.value) or "extra" in str(exc.value).lower()


def test_empty_repo_url_rejected():
    with pytest.raises(ValidationError):
        BuildConfigModel(repo_url="", build_command="make build", output_path="dist")

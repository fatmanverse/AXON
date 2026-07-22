"""DeployRequestBody schema 契约测试(artifact 直接部署 Task 1)。

校验 version 与 artifact_id 必须且只能提供一个的互斥规则。
"""

import pytest
from pydantic import ValidationError

from app.models.deployment import DeploymentStrategy
from app.schemas.service import DeployRequestBody


def test_deploy_request_with_version_only():
    """CI 模式:只传 version,合法。"""
    body = DeployRequestBody(version="v1.2.3")
    assert body.version == "v1.2.3"
    assert body.artifact_id is None
    assert body.strategy == DeploymentStrategy.ROLLING


def test_deploy_request_with_artifact_only():
    """artifact 模式:只传 artifact_id,合法。"""
    body = DeployRequestBody(artifact_id="a" * 32)
    assert body.artifact_id == "a" * 32
    assert body.version is None


def test_deploy_request_with_both_raises():
    """同时传 version 与 artifact_id 抛 ValidationError。"""
    with pytest.raises(ValidationError, match="必须且只能提供一个"):
        DeployRequestBody(version="v1.0", artifact_id="b" * 32)


def test_deploy_request_with_neither_raises():
    """两者都不传抛 ValidationError。"""
    with pytest.raises(ValidationError, match="必须且只能提供一个"):
        DeployRequestBody()


def test_deploy_request_artifact_with_strategy():
    """artifact 模式可指定 strategy。"""
    body = DeployRequestBody(artifact_id="c" * 32, strategy=DeploymentStrategy.RECREATE)
    assert body.strategy == DeploymentStrategy.RECREATE


def test_deploy_request_artifact_with_git_sha():
    """artifact 模式可带 git_sha(用于门禁)。"""
    body = DeployRequestBody(artifact_id="d" * 32, git_sha="abc123")
    assert body.git_sha == "abc123"

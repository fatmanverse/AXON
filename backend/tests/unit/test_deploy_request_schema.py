import pytest
from pydantic import ValidationError

from app.schemas.deployment import DeploymentOut
from app.schemas.service import DeployRequestBody


def test_deploy_request_accepts_version_for_ci_deployment():
    body = DeployRequestBody.model_validate({"version": "v1.0.0"})

    assert body.version == "v1.0.0"
    assert body.artifact_id is None


def test_deploy_request_accepts_artifact_id_without_version():
    artifact_id = "a" * 32

    body = DeployRequestBody.model_validate({"artifact_id": artifact_id})

    assert body.artifact_id == artifact_id
    assert body.version is None


def test_deploy_request_rejects_empty_body():
    with pytest.raises(ValidationError, match="CI 部署需 version"):
        DeployRequestBody.model_validate({})


def test_deployment_out_includes_artifact_id():
    artifact_id = "a" * 32

    deployment = DeploymentOut.model_validate(
        {
            "id": "d" * 32,
            "service_id": "s" * 32,
            "env": "prod",
            "artifact_id": artifact_id,
            "strategy": "rolling",
            "source": "ui-triggered",
            "status": "running",
        }
    )

    assert deployment.artifact_id == artifact_id

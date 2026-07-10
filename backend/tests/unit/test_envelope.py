"""T0.1 验收:统一 API 响应 envelope 与统一异常处理。"""

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.core.responses import ok

router = APIRouter()


@router.get("/_probe/ok")
def _probe_ok() -> dict:
    return ok({"value": 42})


@router.get("/_probe/app-error")
def _probe_app_error() -> dict:
    raise AppError(code="probe_failed", message="故意失败", status_code=422)


@router.get("/_probe/unhandled")
def _probe_unhandled() -> dict:
    raise RuntimeError("boom")


@pytest.fixture
def probe_client() -> TestClient:
    from app.main import create_app

    app: FastAPI = create_app()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_ok_envelope(probe_client: TestClient) -> None:
    body = probe_client.get("/_probe/ok").json()
    assert body == {
        "success": True,
        "data": {"value": 42},
        "error": None,
        "meta": {},
    }


def test_app_error_maps_to_envelope(probe_client: TestClient) -> None:
    resp = probe_client.get("/_probe/app-error")
    assert resp.status_code == 422
    body = resp.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "probe_failed"
    assert body["error"]["message"] == "故意失败"


def test_unhandled_error_is_masked(probe_client: TestClient) -> None:
    resp = probe_client.get("/_probe/unhandled")
    assert resp.status_code == 500
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "internal_error"
    # 不泄漏内部异常细节(§security 错误消息不泄漏敏感信息)
    assert "boom" not in body["error"]["message"]

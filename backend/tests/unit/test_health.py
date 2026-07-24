"""T0.1 验收:/healthz 返回 200,响应走统一 envelope。"""

from fastapi.testclient import TestClient


def test_root_returns_service_entrypoints(client: TestClient) -> None:
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "service": "一脉 Axon 控制面",
        "status": "ok",
        "health": "/healthz",
        "docs": "/docs",
        "api_prefix": "/api",
    }


def test_healthz_returns_200(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_envelope_shape(client: TestClient) -> None:
    body = client.get("/healthz").json()
    # 统一响应 envelope:success / data / error / meta
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["status"] == "ok"
    assert "meta" in body


def test_healthz_reports_component_checks(client: TestClient) -> None:
    data = client.get("/healthz").json()["data"]
    # DB 探活字段先占位(T0.2 接线),此处只要求存在 checks 结构
    assert "checks" in data
    assert isinstance(data["checks"], dict)

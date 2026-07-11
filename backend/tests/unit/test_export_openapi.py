"""T0.11 OpenAPI 导出:后端能生成前端类型来源 schema。"""

import json

from app.cli.export_openapi import export_openapi


def test_export_openapi_writes_schema(tmp_path):
    output = tmp_path / "openapi.json"

    export_openapi(output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["openapi"].startswith("3.")
    assert data["info"]["title"] == "一脉 Axon 控制面"
    assert "/healthz" in data["paths"]
    assert "/api/auth/login" in data["paths"]

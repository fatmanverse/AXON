"""导出 FastAPI OpenAPI schema,供前端生成 TS 类型。"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "openapi.json"


def export_openapi(output: Path = DEFAULT_OUTPUT) -> None:
    app = create_app()
    schema = app.openapi()
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    export_openapi()


if __name__ == "__main__":
    main()

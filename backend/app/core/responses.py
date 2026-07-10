"""统一 API 响应 envelope(§patterns 响应封装)。

所有成功响应走 ok(),失败响应由异常处理器统一转成 fail() 形态,
保证前端只需一套解包逻辑(对应 T0.11 前端解包工具)。
"""

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class Envelope(BaseModel):
    success: bool
    data: Any = None
    error: ErrorBody | None = None
    meta: dict[str, Any] = {}


def ok(data: Any = None, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return Envelope(success=True, data=data, error=None, meta=meta or {}).model_dump()


def fail(
    code: str,
    message: str,
    *,
    details: Any = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return Envelope(
        success=False,
        data=None,
        error=ErrorBody(code=code, message=message, details=details),
        meta=meta or {},
    ).model_dump()

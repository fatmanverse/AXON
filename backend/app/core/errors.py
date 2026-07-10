"""领域异常与统一异常处理器。

AppError 是所有可预期业务错误的基类,携带机器可读 code 与用户可读 message。
未捕获异常一律脱敏为 internal_error,避免泄漏堆栈或内部细节(§security)。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger
from app.core.responses import fail

log = get_logger("errors")


class AppError(Exception):
    """可预期的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _envelope(status_code: int, code: str, message: str, details: object = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=fail(code, message, details=details),
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _envelope(422, "validation_error", "请求参数校验失败", exc.errors())

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
            exc.status_code, "http_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return _envelope(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 只在服务端留详细日志,响应对外脱敏
        log.error(
            "unhandled_exception",
            path=request.url.path,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return _envelope(500, "internal_error", "服务器内部错误")

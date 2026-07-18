"""控制面二进制下载端点(需求4 离线分发)。

目标机经 SSH 自举 node_exporter / axon-agent 时,从此端点拉二进制,不走公网
github。免鉴权只读——目标机在纳管前无 token,且二进制本身非敏感(§离线分发决策)。

安全:严防路径穿越。只允许取 dist_dir 下的**单层文件名**,任何含路径分隔符或
.. 的请求一律拒绝(404),不能逃逸到 dist_dir 之外。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.core.config import Settings
from app.core.errors import AppError

router = APIRouter(prefix="/api/dist", tags=["dist"])


@router.get("/{filename}")
async def download_binary(
    filename: str,
    request: Request,
) -> FileResponse:
    """从控制面预置目录返回二进制文件。免鉴权只读;严防路径穿越。"""
    # 读运行期注入的 settings(app.state,与 webhooks 同规矩),而非模块级
    # 缓存的 get_settings()——后者忽略 create_app(settings) 注入的 dist_dir。
    settings: Settings = request.app.state.settings
    # 只接受单层安全文件名:含分隔符 / 空段 / .. 的一律拒绝,杜绝 ../ 逃逸。
    if filename in ("", ".", "..") or "/" in filename or "\\" in filename or ".." in filename:
        raise AppError("invalid_filename", "非法文件名", status_code=404)

    dist_dir = Path(settings.dist_dir).resolve()
    target = (dist_dir / filename).resolve()
    # 二次防线:解析后必须仍在 dist_dir 内(防符号链接等绕过)。
    if dist_dir not in target.parents and target != dist_dir:
        raise AppError("invalid_filename", "非法文件名", status_code=404)
    if not target.is_file():
        raise AppError("dist_not_found", f"文件不存在: {filename}", status_code=404)

    return FileResponse(path=str(target), filename=filename, media_type="application/octet-stream")

"""node_exporter 自举安装(经 SSH,T1.13,设计 §6.2)。

在纳管机上幂等地安装并拉起 node_exporter:已装则确保运行,未装则按目标版本
下载官方二进制、落 systemd 单元并 enable --now。Agent 接入后由 Agent 自举
(§5.2),SSH 模式下由控制面经本 installer 补齐。

设计要点:
- 幂等:脚本先探测 `command -v node_exporter`,已装直接确保运行,不重复下载。
- 与运行时适配器一样注入 Executor,自身只负责生成/下发脚本,不关心送达方式。
- version 经 shlex.quote 转义,杜绝命令注入;架构固定 linux-amd64(MVP)。
- 安装脚本返回非 0 即抛 AppError,携带 stderr 供上层定位。
"""

from __future__ import annotations

import shlex

from app.adapters.executor import Executor
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("node_exporter")

DEFAULT_VERSION = "1.8.2"
DEFAULT_PORT = 9100
_ARCH = "linux-amd64"


def _bootstrap_script(version: str, port: int, base_url: str | None = None) -> str:
    """生成幂等安装脚本。version 已在调用处转义,这里直接内插。

    脚本语义:已装则仅确保 systemd 拉起;未装则下载解压、装 systemd 单元、
    enable --now。所有步骤在一个 `set -e` 的 sh 里执行,任一步失败即整体非 0。

    base_url 为空走 github 公网;有值则从控制面下载端点拉(离线分发,需求4)。
    """
    quoted_version = shlex.quote(version)
    listen = f":{port}"
    if base_url:
        # 离线分发:控制面下载端点直接托管 <DIR>.tar.gz(dist_dir 下预置的文件名)。
        q_base = shlex.quote(base_url.rstrip("/"))
        download = (
            f"  BASE={q_base}; "
            f'  curl -fsSL -o node_exporter.tar.gz "${{BASE}}/${{DIR}}.tar.gz"; '
        )
    else:
        download = (
            "  BASE=https://github.com/prometheus/node_exporter/releases/download; "
            '  curl -fsSL -o node_exporter.tar.gz "${BASE}/v${VER}/${DIR}.tar.gz"; '
        )
    return (
        "set -e; "
        "if command -v node_exporter >/dev/null 2>&1; then "
        "  systemctl enable --now node_exporter; "
        "else "
        f"  VER={quoted_version}; "
        f"  DIR=node_exporter-${{VER}}.{_ARCH}; "
        f"  cd /tmp; "
        f"{download}"
        f"  tar xzf node_exporter.tar.gz; "
        f"  install -m 0755 ${{DIR}}/node_exporter /usr/local/bin/node_exporter; "
        "  printf '%s\\n' "
        "'[Unit]' 'Description=Prometheus Node Exporter' 'After=network.target' "
        "'[Service]' "
        f"'ExecStart=/usr/local/bin/node_exporter --web.listen-address={listen}' "
        "'Restart=on-failure' 'User=root' "
        "'[Install]' 'WantedBy=multi-user.target' "
        "> /etc/systemd/system/node_exporter.service; "
        "  systemctl daemon-reload; "
        "  systemctl enable --now node_exporter; "
        "fi"
    )


class NodeExporterInstaller:
    """经 SSH 在一台纳管机上幂等安装 node_exporter。"""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def ensure_installed(
        self,
        *,
        version: str = DEFAULT_VERSION,
        port: int = DEFAULT_PORT,
        base_url: str | None = None,
    ) -> None:
        """确保目标机装好并运行 node_exporter。失败抛 AppError。

        base_url 为空走 github 公网;有值则从控制面下载端点拉(离线分发,需求4)。
        """
        script = _bootstrap_script(version, port, base_url)
        result = await self._executor.exec(script)
        if not result.succeeded:
            log.warning(
                "node_exporter_install_failed",
                exit_code=result.exit_code,
            )
            raise AppError(
                "node_exporter_install_failed",
                f"node_exporter 安装失败: {result.stderr.strip()}",
                status_code=502,
            )

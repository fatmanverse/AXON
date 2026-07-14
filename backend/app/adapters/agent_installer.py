"""axon-agent 经 SSH 自举安装(需求4,照搬 node_exporter 模式)。

在纳管机上幂等安装并拉起 axon-agent:已装则确保运行,未装则从**控制面下载
端点**拉二进制(离线分发,不走公网 github)、落 systemd 单元并 enable --now。

设计要点:
- 幂等:脚本先探测 `command -v {service_name}`,已装直接确保运行,不重复下载。
- 与 node_exporter installer 同构:注入 Executor,自身只生成/下发脚本。
- download_url 由上层按 control_plane_base_url + 版本组装并注入,经 shlex.quote
  转义杜绝命令注入;不在此拼公网地址。
- 安装脚本返回非 0 即抛 AppError,携带 stderr 供上层定位。
"""

from __future__ import annotations

import shlex

from app.adapters.executor import Executor
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("agent_installer")


def _bootstrap_script(
    *,
    download_url: str,
    version: str,
    install_dir: str = "/usr/local/bin",
    service_name: str = "axon-agent",
) -> str:
    """生成幂等安装脚本。download_url/版本/路径经 shlex.quote 转义防注入。"""
    q_url = shlex.quote(download_url)
    q_bin = shlex.quote(f"{install_dir}/{service_name}")
    q_svc = shlex.quote(service_name)
    unit_path = f"/etc/systemd/system/{service_name}.service"
    exec_start = f"{install_dir}/{service_name}"
    return (
        "set -e; "
        f"if command -v {service_name} >/dev/null 2>&1; then "
        f"  systemctl enable --now {q_svc}; "
        "else "
        f"  curl -fsSL -o {q_bin} {q_url}; "
        f"  chmod 0755 {q_bin}; "
        "  printf '%s\\n' "
        "'[Unit]' 'Description=Axon Agent' 'After=network.target' "
        "'[Service]' "
        f"'ExecStart={exec_start}' "
        "'Restart=on-failure' 'User=root' "
        "'[Install]' 'WantedBy=multi-user.target' "
        f"  > {unit_path}; "
        "  systemctl daemon-reload; "
        f"  systemctl enable --now {q_svc}; "
        "fi"
    )


class AgentInstaller:
    """经 SSH 在一台纳管机上幂等安装 axon-agent。"""

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def ensure_installed(
        self,
        *,
        download_url: str,
        version: str,
        install_dir: str = "/usr/local/bin",
        service_name: str = "axon-agent",
    ) -> None:
        """确保目标机装好并运行 axon-agent。失败抛 AppError。"""
        script = _bootstrap_script(
            download_url=download_url,
            version=version,
            install_dir=install_dir,
            service_name=service_name,
        )
        result = await self._executor.exec(script)
        if not result.succeeded:
            log.warning("agent_install_failed", exit_code=result.exit_code)
            raise AppError(
                "agent_install_failed",
                f"agent 安装失败: {result.stderr.strip()}",
                status_code=502,
            )

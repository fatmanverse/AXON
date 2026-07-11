"""Prometheus file_sd 目标登记(T1.13,设计 §6.2)。

纳管机自举 node_exporter 后,把它的抓取目标写进 Prometheus file_sd JSON。
Prometheus 按 refresh_interval 重读该文件即发现新目标,无需 reload(§6.2)。

file_sd 标准结构(见 prometheus.yml 的 file_sd_configs):
    [{"targets": ["host:port"], "labels": {...}}, ...]

设计要点:
- 幂等:按 "host:port" 唯一定位,重复登记只更新 labels,不新增条目。
- 原子写:先写同目录临时文件再 os.replace 覆盖,避免 Prometheus 读到半截 JSON。
- 容错读:文件缺失/内容损坏时按空列表起步,不因坏数据阻断新登记。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger("prometheus_targets")

Entry = dict[str, Any]


class PrometheusTargetRegistry:
    """维护单个 file_sd JSON 文件的目标增删。"""

    def __init__(self, sd_file: Path) -> None:
        self._sd_file = Path(sd_file)

    def register(self, *, host: str, port: int, labels: dict[str, str]) -> None:
        """登记一个抓取目标。同 host:port 已存在则更新其 labels(幂等)。"""
        target = f"{host}:{port}"
        entries = self._load()
        entries = [e for e in entries if e.get("targets") != [target]]
        entries.append({"targets": [target], "labels": dict(labels)})
        self._write(entries)

    def unregister(self, *, host: str, port: int) -> None:
        """移除一个抓取目标。不存在则静默(幂等)。"""
        target = f"{host}:{port}"
        entries = [e for e in self._load() if e.get("targets") != [target]]
        self._write(entries)

    def _load(self) -> list[Entry]:
        if not self._sd_file.exists():
            return []
        try:
            data = json.loads(self._sd_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("prometheus_sd_unreadable", path=str(self._sd_file))
            return []
        return data if isinstance(data, list) else []

    def _write(self, entries: list[Entry]) -> None:
        self._sd_file.parent.mkdir(parents=True, exist_ok=True)
        # 同目录临时文件 + os.replace:同一文件系统内的原子替换,Prometheus
        # 永远读到完整 JSON(半写的临时文件不会被 file_sd 读到)。
        fd, tmp_path = tempfile.mkstemp(dir=self._sd_file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp_path, self._sd_file)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

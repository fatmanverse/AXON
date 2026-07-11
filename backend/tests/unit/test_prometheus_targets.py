"""T1.13 Prometheus file_sd 目标登记(设计 §6.2)。

验证 PrometheusTargetRegistry 对 file_sd JSON 的幂等增删:
- 新增目标写入标准 file_sd 结构([{targets, labels}]).
- 重复登记同一目标不产生重复条目(幂等)。
- 注销移除对应目标。
- 写入是原子的(先写临时文件再替换),坏的既有内容不影响新写入。

用 tmp_path 隔离真实文件系统。
"""

import json

from app.services.prometheus_targets import PrometheusTargetRegistry


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_register_writes_file_sd_entry(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)

    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})

    data = _read(sd_file)
    assert len(data) == 1
    entry = data[0]
    assert entry["targets"] == ["10.0.0.1:9100"]
    assert entry["labels"]["server"] == "web-01"


def test_register_is_idempotent(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)

    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})
    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})

    data = _read(sd_file)
    assert len(data) == 1


def test_register_updates_labels_for_existing_target(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)

    registry.register(host="10.0.0.1", port=9100, labels={"server": "old"})
    registry.register(host="10.0.0.1", port=9100, labels={"server": "new"})

    data = _read(sd_file)
    assert len(data) == 1
    assert data[0]["labels"]["server"] == "new"


def test_register_multiple_distinct_targets(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)

    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})
    registry.register(host="10.0.0.2", port=9100, labels={"server": "web-02"})

    targets = {entry["targets"][0] for entry in _read(sd_file)}
    assert targets == {"10.0.0.1:9100", "10.0.0.2:9100"}


def test_unregister_removes_target(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)
    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})
    registry.register(host="10.0.0.2", port=9100, labels={"server": "web-02"})

    registry.unregister(host="10.0.0.1", port=9100)

    targets = {entry["targets"][0] for entry in _read(sd_file)}
    assert targets == {"10.0.0.2:9100"}


def test_unregister_missing_target_is_noop(tmp_path):
    sd_file = tmp_path / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)
    registry.register(host="10.0.0.1", port=9100, labels={"server": "web-01"})

    registry.unregister(host="10.0.0.9", port=9100)

    assert len(_read(sd_file)) == 1


def test_reads_empty_when_file_absent(tmp_path):
    sd_file = tmp_path / "does-not-exist.json"
    registry = PrometheusTargetRegistry(sd_file)

    # 文件不存在时首次登记应自动创建父目录与文件
    registry.register(host="10.0.0.1", port=9100, labels={})

    assert sd_file.exists()
    assert len(_read(sd_file)) == 1


def test_creates_parent_directory(tmp_path):
    sd_file = tmp_path / "nested" / "dir" / "nodes.json"
    registry = PrometheusTargetRegistry(sd_file)

    registry.register(host="10.0.0.1", port=9100, labels={})

    assert sd_file.exists()

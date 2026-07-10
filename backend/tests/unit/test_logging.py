"""T0.1 验收:结构化日志为 JSON,含请求追踪字段。"""

import json

from app.core.logging import configure_logging, get_logger


def test_logging_emits_json(capsys) -> None:
    configure_logging(json_logs=True, level="INFO")
    log = get_logger("test")
    log.info("hello", request_id="req-123", path="/healthz")

    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)  # 必须是合法 JSON

    assert payload["event"] == "hello"
    assert payload["request_id"] == "req-123"
    assert payload["path"] == "/healthz"
    assert payload["level"] == "info"

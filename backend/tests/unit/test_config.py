"""T0.1 验收:配置从环境变量加载(pydantic-settings)。"""

from app.core.config import Settings


def test_settings_defaults() -> None:
    s = Settings()
    assert s.app_name
    assert s.env in {"dev", "staging", "prod"}


def test_settings_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("YIMAI_ENV", "staging")
    monkeypatch.setenv("YIMAI_LOG_JSON", "false")
    s = Settings()
    assert s.env == "staging"
    assert s.log_json is False

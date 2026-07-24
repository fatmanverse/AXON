from app.core.config import Settings
from app.workers import deploy_tasks


async def test_worker_reconcile_skips_when_pipeline_config_is_empty(monkeypatch):
    deploy_tasks.set_provider_resolver(None)
    monkeypatch.setattr(
        deploy_tasks,
        "get_settings",
        lambda: Settings(pipeline_config={}),
    )

    result = await deploy_tasks._run_once()

    assert result == {"skipped": True, "reconciled": 0}

"""部署操作 task 的数据库级互斥约束。"""

from sqlalchemy import inspect

from app.models.task import Task


def test_task_model_declares_active_deployment_partial_unique_index():
    indexes = {index.name: index for index in inspect(Task).local_table.indexes}

    index = indexes["uq_tasks_active_deployment_target"]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["target"]
    assert "DEPLOY" in str(index.dialect_options["postgresql"]["where"])
    assert "ROLLBACK" in str(index.dialect_options["postgresql"]["where"])
    assert "PENDING" in str(index.dialect_options["sqlite"]["where"])
    assert "RUNNING" in str(index.dialect_options["sqlite"]["where"])

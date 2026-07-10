"""T0.2 数据库基线单测:引擎/会话/事务上下文,用 aiosqlite 内存库。"""

import pytest
from sqlalchemy import text

from app.core.db import Database


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    yield database
    await database.dispose()


async def test_session_executes_query(db):
    async with db.session() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_transaction_commits_on_success(db):
    async with db.session() as session:
        await session.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
        await session.execute(text("INSERT INTO t (v) VALUES ('a')"))

    async with db.session() as session:
        result = await session.execute(text("SELECT count(*) FROM t"))
        assert result.scalar_one() == 1


async def test_transaction_rolls_back_on_error(db):
    async with db.session() as session:
        await session.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))

    with pytest.raises(RuntimeError):
        async with db.session() as session:
            await session.execute(text("INSERT INTO t (v) VALUES ('b')"))
            raise RuntimeError("boom")

    async with db.session() as session:
        result = await session.execute(text("SELECT count(*) FROM t"))
        assert result.scalar_one() == 0


async def test_ping_returns_true_when_reachable(db):
    assert await db.ping() is True

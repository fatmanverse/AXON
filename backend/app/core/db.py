"""异步数据库引擎与会话管理。

生产走 PostgreSQL(asyncpg),本地测试走 sqlite+aiosqlite。
`session()` 上下文管理器统一事务边界:正常提交,异常回滚。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """封装 async engine + sessionmaker,提供带事务边界的 session。"""

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 10) -> None:
        # sqlite 内存库不接受连接池参数,按方言分流。
        if url.startswith("sqlite"):
            self._engine: AsyncEngine = create_async_engine(url, echo=echo)
        else:
            self._engine = create_async_engine(
                url,
                echo=echo,
                pool_size=pool_size,
                pool_pre_ping=True,
            )
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """事务边界:块正常结束提交,抛异常回滚,始终关闭会话。

        实时推送(T0.10):repo 层把待推消息暂存到本会话 outbox;仅在 commit 成功后
        才 flush 到 WebSocket Hub(未提交/回滚的状态不外泄给前端)。flush 自身吞异常,
        绝不影响业务写。
        """
        from app.core import realtime

        session = self._sessionmaker()
        token = realtime.open_outbox()
        committed = False
        try:
            yield session
            await session.commit()
            committed = True
        except Exception:
            await session.rollback()
            raise
        finally:
            pending = realtime.reset_outbox(token)
            await session.close()
            if committed:
                await realtime.flush(pending)

    async def ping(self) -> bool:
        """DB 探活:健康检查用,连不通返回 False。"""
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self._engine.dispose()

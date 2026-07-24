"""种子数据:创建初始管理员账号。

用法(迁移后执行):
    uv run python -m app.cli.seed
账号密码从环境变量读取(YIMAI_SEED_ADMIN_USER / YIMAI_SEED_ADMIN_PASSWORD),
默认 admin / admin。生产务必改密并通过环境注入,不写死。
"""

import asyncio

from app.core.config import get_settings
from app.core.db import Database
from app.services.auth_service import AuthService


async def seed_admin() -> None:
    settings = get_settings()
    settings.validate_for_runtime()
    username = settings.seed_admin_user
    password = settings.seed_admin_password

    db = Database(settings.database_url, echo=settings.db_echo, pool_size=settings.db_pool_size)
    try:
        async with db.session() as session:
            svc = AuthService(session, settings)
            existing = await svc.get_by_username(username)
            if existing is not None:
                print(f"管理员 {username!r} 已存在,跳过。")
                return
            await svc.create_user(username, password, roles=["admin"])
            print(f"已创建管理员 {username!r}(角色 admin)。请尽快改密。")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(seed_admin())

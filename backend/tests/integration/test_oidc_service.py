"""OidcService 账号映射与令牌签发验收(块五:通用 OIDC 第三方登录)。

用 fake claims(不依赖真实 IdP),验证:
- 首次登录按 (provider, subject) 自动建号 + 建绑定 + 赋默认只读角色;
- 二次登录复用同一账号(不重复建号);
- username 冲突时加后缀去重;
- 签发的是本系统合法 JWT(可被 decode_access_token 校验);
- 自动建号的用户拿到 oidc_default_role 只读角色。
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import Settings
from app.core.db import Database
from app.core.security import decode_access_token
from app.models.base import Base
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.services.oidc_service import OidcClaims, OidcService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db():
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        log_json=False,
        jwt_secret="itest-secret-oidc-at-least-32-bytes-long",
        secret_backend="local",
        secret_master_key="",
        oidc_enabled=True,
        oidc_provider="keycloak",
        oidc_default_role="viewer",
    )
    database = Database(settings.database_url, echo=False)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database, settings
    await database.dispose()


async def test_first_login_provisions_user_and_binds_identity(db):
    database, settings = db
    async with database.session() as session:
        svc = OidcService(session, settings)
        user, token = await svc.login_with_claims(
            OidcClaims(subject="sub-1", email="alice@example.com", preferred_username="alice")
        )
        assert user.username == "alice"
        assert token
        rows = (await session.execute(select(UserIdentity))).scalars().all()
        assert len(rows) == 1
        assert rows[0].provider == "keycloak"
        assert rows[0].subject == "sub-1"
        assert rows[0].user_id == user.id


async def test_second_login_reuses_same_user(db):
    database, settings = db
    async with database.session() as session:
        svc = OidcService(session, settings)
        u1, _ = await svc.login_with_claims(OidcClaims(subject="sub-1", preferred_username="alice"))
        u2, _ = await svc.login_with_claims(OidcClaims(subject="sub-1", preferred_username="alice"))
        assert u1.id == u2.id
        rows = (await session.execute(select(UserIdentity))).scalars().all()
        assert len(rows) == 1


async def test_username_collision_gets_suffixed(db):
    database, settings = db
    async with database.session() as session:
        # 预置一个占用 "alice" 的本地用户
        session.add(User(username="alice", password_hash="x"))
        await session.flush()
        svc = OidcService(session, settings)
        user, _ = await svc.login_with_claims(
            OidcClaims(subject="sub-9", preferred_username="alice")
        )
        assert user.username != "alice"
        assert user.username.startswith("alice")


async def test_issued_token_is_valid_system_jwt(db):
    database, settings = db
    async with database.session() as session:
        svc = OidcService(session, settings)
        _, token = await svc.login_with_claims(OidcClaims(subject="sub-2", preferred_username="bob"))
        claims = decode_access_token(token, settings.jwt_secret)
        assert claims["sub"] == "bob"


async def test_provisioned_user_gets_default_readonly_role(db):
    database, settings = db
    async with database.session() as session:
        svc = OidcService(session, settings)
        user, _ = await svc.login_with_claims(
            OidcClaims(subject="sub-3", preferred_username="carol")
        )
        assert [r.name for r in user.roles] == ["viewer"]

"""密码哈希(argon2)与 JWT 签发/校验。"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    roles: list[str] = field(default_factory=list)
    token_version: int = 0


def create_access_token(
    *,
    subject: str,
    secret: str,
    roles: list[str],
    algorithm: str = "HS256",
    expires_minutes: int = 480,
    token_version: int = 0,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "roles": roles,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> TokenClaims:
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    return TokenClaims(
        subject=payload["sub"],
        roles=list(payload.get("roles", [])),
        token_version=int(payload.get("ver", 0)),
    )

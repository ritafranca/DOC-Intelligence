from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User, UserRole


password_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,
)

ROLE_PERMISSIONS: dict[UserRole, frozenset[str]] = {
    UserRole.ADMIN: frozenset(
        {"document.submit", "document.read", "document.review", "document.admin"}
    ),
    UserRole.OPERATOR: frozenset(
        {"document.submit", "document.read", "document.review"}
    ),
}


def normalize_email(value: str) -> str:
    return value.strip().lower()


def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("A senha deve ter no máximo 72 bytes.")
    return password_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    if len(password.encode("utf-8")) > 72:
        return False
    try:
        return password_context.verify(password, hashed_password)
    except (TypeError, ValueError):
        return False


def permissions_for_role(role: UserRole) -> frozenset[str]:
    return ROLE_PERMISSIONS[role]


def create_access_token(user: User) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires_delta = timedelta(minutes=settings.jwt_access_token_minutes)
    expires_at = now + expires_delta
    payload = {
        "sub": user.id,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
        "role": user.role.value,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti"]},
    )


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    user = await session.scalar(select(User).where(User.email == normalize_email(email)))
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


async def ensure_default_admin(session: AsyncSession) -> None:
    email = normalize_email(settings.default_admin_email)
    if await session.scalar(select(User.id).where(User.email == email)):
        return
    admin = User(
        name=settings.default_admin_name.strip() or "Administrador",
        email=email,
        hashed_password=hash_password(settings.default_admin_password),
        role=UserRole.ADMIN,
    )
    session.add(admin)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()

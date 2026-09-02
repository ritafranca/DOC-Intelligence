from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import decode_access_token, permissions_for_role
from app.config import settings
from app.database import get_session
from app.models import User, UserRole


ROLE_SUBMIT = "document.submit"
ROLE_READ = "document.read"
ROLE_REVIEW = "document.review"
ROLE_ADMIN = "document.admin"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    email: str | None
    name: str | None
    roles: frozenset[str]
    role: UserRole | None = None

    def has_role(self, role: str) -> bool:
        return ROLE_ADMIN in self.roles or role in self.roles


class OIDCVerifier:
    def __init__(self) -> None:
        self._jwks: dict | None = None
        self._jwks_uri: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _load_jwks(self) -> dict:
        if self._jwks and time.monotonic() < self._expires_at:
            return self._jwks
        async with self._lock:
            if self._jwks and time.monotonic() < self._expires_at:
                return self._jwks
            async with httpx.AsyncClient(timeout=10) as client:
                discovery = await client.get(
                    f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"
                )
                discovery.raise_for_status()
                self._jwks_uri = discovery.json()["jwks_uri"]
                response = await client.get(self._jwks_uri)
                response.raise_for_status()
                self._jwks = response.json()
                self._expires_at = time.monotonic() + 600
            return self._jwks

    async def verify(self, token: str) -> dict:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if not key_id:
            raise ValueError("Token sem kid.")
        jwks = await self._load_jwks()
        jwk = next((item for item in jwks.get("keys", []) if item.get("kid") == key_id), None)
        if not jwk:
            self._expires_at = 0
            jwks = await self._load_jwks()
            jwk = next((item for item in jwks.get("keys", []) if item.get("kid") == key_id), None)
        if not jwk:
            raise ValueError("Chave de assinatura não encontrada.")
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        return jwt.decode(
            token,
            key=public_key,
            algorithms=list(settings.oidc_algorithms),
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )


verifier = OIDCVerifier()
bearer = HTTPBearer(auto_error=False)


def _extract_roles(claims: dict) -> frozenset[str]:
    roles = set(claims.get("roles", []))
    roles.update(claims.get("realm_access", {}).get("roles", []))
    for client_data in claims.get("resource_access", {}).values():
        roles.update(client_data.get("roles", []))
    return frozenset(str(role) for role in roles)


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido ou expirado.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _resolve_local_user(
    credentials: HTTPAuthorizationCredentials | None,
    session: AsyncSession,
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação obrigatória.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials)
        user = await session.get(User, str(claims["sub"]))
    except Exception as exc:
        raise _authentication_error() from exc
    if not user:
        raise _authentication_error()
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    return await _resolve_local_user(credentials, session)


async def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_dev_user: str | None = Header(default=None),
    x_dev_roles: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if settings.auth_disabled:
        if settings.environment == "production":
            raise HTTPException(status_code=500, detail="Configuração de autenticação inválida.")
        roles = frozenset(
            role.strip()
            for role in (
                x_dev_roles
                or f"{ROLE_SUBMIT},{ROLE_READ},{ROLE_REVIEW},{ROLE_ADMIN}"
            ).split(",")
            if role.strip()
        )
        return Principal(
            subject=(x_dev_user or "dev.operator").strip(),
            email=f"{(x_dev_user or 'dev.operator').strip()}@local",
            name="Operador de desenvolvimento",
            roles=roles,
            role=UserRole.ADMIN if ROLE_ADMIN in roles else UserRole.OPERATOR,
        )
    if settings.auth_provider == "local":
        user = await _resolve_local_user(credentials, session)
        return Principal(
            subject=user.id,
            email=user.email,
            name=user.name,
            roles=permissions_for_role(user.role),
            role=user.role,
        )
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação OIDC obrigatória.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = await verifier.verify(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return Principal(
        subject=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
        roles=_extract_roles(claims),
        role=(
            UserRole.ADMIN
            if ROLE_ADMIN in _extract_roles(claims)
            else UserRole.OPERATOR
        ),
    )


def require_role(role: str):
    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_role(role):
            raise HTTPException(status_code=403, detail=f"Permissão necessária: {role}")
        return principal

    return dependency

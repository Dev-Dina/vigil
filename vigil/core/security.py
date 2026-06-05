"""Password hashing + JWT mint/verify. Signing key comes from Vault (config), never inline.

This module is the only place that knows how to hash a password or sign/verify a token.
JWTs carry the full scope payload (api.md). Verification is strict: bad signature, wrong
issuer, or expiry all raise — errors never pass silently.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from vigil.core.config import get_settings
from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role

_hasher = PasswordHasher()


class TokenError(Exception):
    """Raised on any token failure (bad signature, expired, revoked-shape). 401."""


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(plaintext: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plaintext)
    except VerifyMismatchError:
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


@dataclass(frozen=True, slots=True)
class MintedToken:
    access_token: str
    jti: str
    expires_in: int


def mint_access_token(scope: Scope, *, jti: str | None = None) -> MintedToken:
    """Sign a JWT carrying identity + role + resolved scope (api.md claim shape)."""
    settings = get_settings()
    now = int(time.time())
    ttl = settings.access_token_ttl_seconds
    session_id = jti or f"sess_{uuid.uuid4().hex[:12]}"
    claims = {
        "iss": settings.jwt_issuer,
        "sub": scope.user_id,
        "iat": now,
        "exp": now + ttl,
        "jti": session_id,
        "role": str(scope.role),
        "home_sponsor_id": scope.home_sponsor_id,
        "home_cro_id": scope.home_cro_id,
        "scope": [
            {"sponsor_id": t.sponsor_id, "trial_id": t.trial_id, "site_id": t.site_id}
            for t in scope.tuples
        ],
        "scope_ver": scope.scope_ver,
    }
    token = jwt.encode(
        claims, settings.jwt_signing_key, algorithm=settings.jwt_algorithm
    )
    return MintedToken(access_token=token, jti=session_id, expires_in=ttl)


def decode_token(token: str) -> dict:
    """Verify signature + issuer + expiry, return raw claims. Raises TokenError on failure."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc


def scope_from_claims(claims: dict) -> Scope:
    """Rebuild the immutable Scope from verified claims. The client never asserts scope."""
    tuples = tuple(
        ScopeTuple(
            sponsor_id=item["sponsor_id"],
            trial_id=item.get("trial_id"),
            site_id=item.get("site_id"),
        )
        for item in claims.get("scope", [])
    )
    return Scope(
        user_id=claims["sub"],
        role=Role(claims["role"]),
        home_sponsor_id=claims.get("home_sponsor_id"),
        home_cro_id=claims.get("home_cro_id"),
        tuples=tuples,
        scope_ver=int(claims.get("scope_ver", 1)),
    )

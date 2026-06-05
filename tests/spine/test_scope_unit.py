"""Pure-unit checks for scope/JWT logic (no DB, always run). Backstop for the subset rule."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("VIGIL_SECRETS_BACKEND", "env")
os.environ.setdefault("VIGIL_JWT_SIGNING_KEY", "test-signing-key-not-a-secret")
os.environ.setdefault("VIGIL_LLM_API_KEY", "test-llm-key")
os.environ.setdefault("VIGIL_DB_DSN", "postgresql+psycopg://x:y@localhost:1/none")

from vigil.core.scope import Scope, ScopeError, ScopeTuple  # noqa: E402
from vigil.core.security import (  # noqa: E402
    mint_access_token,
    scope_from_claims,
    decode_token,
)
from vigil.domain import Role  # noqa: E402


def _sponsor_scope(sponsor: str) -> Scope:
    return Scope(
        user_id="usr_1",
        role=Role.SPONSOR_OVERSIGHT,
        home_sponsor_id=sponsor,
        home_cro_id=None,
        tuples=(ScopeTuple(sponsor_id=sponsor),),
        scope_ver=1,
    )


def test_sponsor_tuple_contains_widened_children():
    s = _sponsor_scope("A")
    assert s.permits(ScopeTuple("A", "t1", "s1"))
    assert not s.permits(ScopeTuple("B"))


def test_narrow_tuple_does_not_cover_sibling():
    narrow = ScopeTuple("A", "t1", "s1")
    assert narrow.contains(ScopeTuple("A", "t1", "s1"))
    assert not narrow.contains(ScopeTuple("A", "t1", "s2"))


def test_subset_rule_rejects_out_of_tenant_grant():
    s = _sponsor_scope("A")
    assert not s.covers(ScopeTuple("B", "t9"))


def test_platform_scope_has_no_sponsor_binding():
    s = Scope("usr_p", Role.PLATFORM_ADMIN, None, None, (), 1)
    assert s.is_platform
    assert s.rls_sponsor_id() is None


def test_cro_multi_sponsor_requires_explicit_selection():
    s = Scope(
        "usr_c",
        Role.STUDY_MANAGER,
        None,
        None,
        (ScopeTuple("A"), ScopeTuple("B")),
        2,
    )
    with pytest.raises(ScopeError):
        s.rls_sponsor_id()  # ambiguous
    assert s.rls_sponsor_id("A") == "A"
    with pytest.raises(ScopeError):
        s.rls_sponsor_id("C")  # outside scope


def test_jwt_roundtrip_preserves_scope():
    s = _sponsor_scope("A")
    minted = mint_access_token(s)
    claims = decode_token(minted.access_token)
    rebuilt = scope_from_claims(claims)
    assert rebuilt.role is Role.SPONSOR_OVERSIGHT
    assert rebuilt.sponsor_ids == {"A"}
    assert claims["iss"] == "vigil-auth"

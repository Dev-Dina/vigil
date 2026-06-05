"""The one way to represent and resolve authorization scope (CLAUDE.md: one obvious way).

A :class:`Scope` is the request-scoped authorization object derived ONLY from verified JWT
claims — never from client input. It answers two questions:

1. *Which sponsor does this request bind to for RLS?* → :meth:`Scope.rls_sponsor_id`.
2. *Is a requested (sponsor, trial?, site?) within reach?* → :meth:`Scope.permits`.

Scope resolution (DB rows → tuples) lives in the auth service; this module is pure value
objects + the subset check, so it is trivially testable with no DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from vigil.domain import PLATFORM_ROLES, Role


class ScopeError(Exception):
    """Raised when a request reaches outside its scope. Surfaces as 403 scope_denied."""


@dataclass(frozen=True, slots=True)
class ScopeTuple:
    """One reachable slice. ``None`` for trial_id/site_id widens to all under the parent."""

    sponsor_id: str
    trial_id: str | None = None
    site_id: str | None = None

    def contains(self, other: ScopeTuple) -> bool:
        """True if ``other`` is a subset of this tuple (used by RLS admit + subset rule)."""
        if self.sponsor_id != other.sponsor_id:
            return False
        if self.trial_id is not None and self.trial_id != other.trial_id:
            return False
        if self.site_id is not None and self.site_id != other.site_id:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Scope:
    """Resolved request authorization. Built from the verified token, immutable."""

    user_id: str
    role: Role
    home_sponsor_id: str | None
    home_cro_id: str | None
    tuples: tuple[ScopeTuple, ...]
    scope_ver: int

    @property
    def is_platform(self) -> bool:
        return self.role in PLATFORM_ROLES

    @property
    def sponsor_ids(self) -> frozenset[str]:
        return frozenset(t.sponsor_id for t in self.tuples)

    def rls_sponsor_id(self, requested_sponsor: str | None = None) -> str | None:
        """The sponsor UUID to set as ``app.current_sponsor`` for this request.

        Platform users carry no sponsor scope → ``None`` (they reach platform/global tables
        and bespoke cross-tenant tables via the is_platform GUC, never tenant rows). For a
        scoped user, default to their single sponsor; if ``requested_sponsor`` is given it
        must be within scope.
        """
        if self.is_platform:
            return None
        if requested_sponsor is not None:
            if requested_sponsor not in self.sponsor_ids:
                raise ScopeError(f"sponsor {requested_sponsor} outside scope")
            return requested_sponsor
        sponsors = self.sponsor_ids
        if len(sponsors) != 1:
            raise ScopeError(
                "ambiguous sponsor scope; a sponsor must be specified for this request"
            )
        return next(iter(sponsors))

    def permits(self, requested: ScopeTuple) -> bool:
        """True if the requested slice is within at least one of the caller's tuples."""
        return any(t.contains(requested) for t in self.tuples)

    def require(self, requested: ScopeTuple) -> None:
        if not self.permits(requested):
            raise ScopeError(f"scope denied for {requested}")

    def covers(self, candidate: ScopeTuple) -> bool:
        """Subset rule for scoped administration: a grantor may only create/grant a scope
        that is a subset of their own. Identical semantics to :meth:`permits`, named for
        the admin path so intent is explicit at call sites."""
        return self.permits(candidate)

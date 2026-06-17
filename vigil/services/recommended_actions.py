"""Recommended protocol ACTIONS — operational coordinator next-steps (Phase 9, Gate 9.3).

The "what to do about it" layer on the at-risk detail. Given a participant's risk band + their
9.1 model-attribution factors, this produces a small ordered list of SUGGESTED COORDINATOR ACTIONS,
mapped to the existing audited ``InterventionKind`` taxonomy (call / visit_reschedule / reminder /
note). These are OPERATIONAL next-steps for a study coordinator — "reach out", "send a reminder",
"flag for clinic follow-up" — **NOT clinical/medical advice, NOT a diagnosis, NOT treatment**.

Design (honesty):
- A STATIC, TRANSPARENT mapping (the catalog below) — no LLM call, no generated/free clinical text.
  Every action string comes from :data:`APPROVED_ACTION_TEXTS`; nothing else can be surfaced.
- Actions are SUGGESTIONS only. Acting on one still goes through the audited
  ``POST /participants/{id}/interventions`` (the coordinator chooses); the suggestion merely
  pre-fills the ``intervention_kind``. Nothing is auto-logged here.
- The synthetic label still governs the surface where the risk is synthetic (carried by the
  enclosing ``RiskExplanation.synthetic`` — the actions are a demonstration of the operational
  response, labelled synthetic).
"""

from __future__ import annotations

from dataclasses import dataclass

from vigil.domain import InterventionKind


@dataclass(frozen=True, slots=True)
class RecommendedAction:
    """One suggested coordinator action (operational, not clinical)."""

    action: str  # human operational text — MUST be from APPROVED_ACTION_TEXTS
    intervention_kind: (
        str  # an InterventionKind value to pre-fill the audited /interventions log
    )
    factor: str | None = None  # the attribution factor it responds to (None = baseline)


# --- the approved operational catalog (the ONLY action texts that can ever surface) ----------
_CONTACT_MISSED = "Contact participant about missed visits"
_SEND_REMINDER = "Send a visit reminder to the participant"
_OFFER_RESCHEDULE = "Offer to reschedule the next visit"
_CLINIC_FOLLOWUP = "Flag for site/clinic follow-up"

#: The closed set of operational action strings. A surfaced action MUST be in this set — this is
#: what guarantees no free-form / clinical text can leak onto the surface (asserted in tests).
APPROVED_ACTION_TEXTS: frozenset[str] = frozenset(
    {_CONTACT_MISSED, _SEND_REMINDER, _OFFER_RESCHEDULE, _CLINIC_FOLLOWUP}
)

# Attribution-factor → action mapping (transparent + deterministic). Factor names are the 9.1
# sequence-attribution features (SEQ_NUMERIC). The missed-visit family routes to an outreach call;
# a reminder is added when attendance is the driver; visit cadence routes to a reschedule offer.
_MISSED_FAMILY: frozenset[str] = frozenset(
    {"consecutive_missed", "cumulative_missed", "missed"}
)


def recommend(risk_band: str, factors: list[str]) -> list[RecommendedAction]:
    """Return ordered suggested coordinator actions for a participant.

    Deterministic + transparent: derived only from the band + the (already-computed) attribution
    factor names; no model call, no fabricated text. Returns actions ONLY for the serious-risk
    (``high``) band — the at-risk surface; lower bands return no suggested actions. The baseline
    clinic-follow-up is always offered for a high participant; factor-driven outreach is added
    (and de-duplicated) ahead of it in factor order.
    """
    if risk_band != "high":
        return []

    actions: list[RecommendedAction] = []
    seen: set[str] = set()

    def _add(text: str, kind: InterventionKind, factor: str | None) -> None:
        if text in seen:
            return
        seen.add(text)
        actions.append(
            RecommendedAction(action=text, intervention_kind=kind.value, factor=factor)
        )

    for f in factors:
        if f in _MISSED_FAMILY:
            _add(_CONTACT_MISSED, InterventionKind.CALL, f)
            _add(_SEND_REMINDER, InterventionKind.REMINDER, f)
        elif f == "visit_index":
            _add(_OFFER_RESCHEDULE, InterventionKind.VISIT_RESCHEDULE, f)

    # Baseline operational step for any serious-risk participant (last).
    _add(_CLINIC_FOLLOWUP, InterventionKind.NOTE, None)
    return actions

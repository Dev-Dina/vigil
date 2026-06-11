"""monitoring router (api.md /monitoring) — model routing administration.

Platform-scoped. The model-promotion endpoint is the manual, platform_admin-only path for
champion/challenger/shadow promotion (specs/routing.md § Audited promotion). Drift-triggered
fallback is NOT here — it is automatic and system-initiated (routing_service.handle_breach),
consuming an opaque breach signal whose delivery is deferred to the observability phase.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.scope import Scope
from vigil.services import routing_service
from vigil.services.routing_service import PromotionError, RoutingPermissionError

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


class ModelPromoteIn(BaseModel):
    regime: str
    model_version: str
    model_card_ref: str = Field(min_length=1)
    eval_provenance: str = Field(min_length=1)
    reason: str = "manual_promotion"


class ModelPromoteOut(BaseModel):
    regime: str
    from_version: str | None
    to_version: str
    actor_user_id: str
    audit_id: str


@router.post(
    "/models/promote",
    response_model=ModelPromoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def promote_model(
    body: ModelPromoteIn, scope: Scope = ScopeDep
) -> ModelPromoteOut:
    """POST /monitoring/models/promote — manual champion promotion (platform_admin only).

    403 for any non-platform_admin caller; 400 on honesty/safety violations.
    """
    try:
        result = routing_service.promote(
            scope,
            regime=body.regime,
            model_version=body.model_version,
            model_card_ref=body.model_card_ref,
            eval_provenance=body.eval_provenance,
            reason=body.reason,
        )
    except RoutingPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except PromotionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return ModelPromoteOut(
        regime=result.regime,
        from_version=result.from_version,
        to_version=result.to_version,
        actor_user_id=result.actor_user_id,
        audit_id=result.audit_id,
    )

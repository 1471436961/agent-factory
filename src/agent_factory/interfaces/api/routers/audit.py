"""Read-only audit trail query route."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from agent_factory.application.queries import AuditQuery, Page
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.enums import AuditEntityType, AuditEventType
from agent_factory.interfaces.api.dependencies import ControllerDep

router = APIRouter(prefix="/audit-events", tags=["audit"])


@router.get("", response_model=Page[AuditEvent])
async def query_audit(
    controller: ControllerDep,
    entity_type: AuditEntityType | None = None,
    entity_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    event_types: Annotated[
        list[AuditEventType] | None,
        Query(alias="event_type"),
    ] = None,
    actor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    created_from: AwareDatetime | None = None,
    created_to: AwareDatetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AuditEvent]:
    query = AuditQuery(
        entity_type=entity_type,
        entity_id=entity_id,
        event_types=frozenset(event_types or ()),
        actor=actor,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    )
    return await controller.query_audit(query)

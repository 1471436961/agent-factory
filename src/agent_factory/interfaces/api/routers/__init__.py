"""FastAPI routers for the M1 Agent Factory contract."""

from fastapi import APIRouter

from agent_factory.interfaces.api.routers.audit import router as audit_router
from agent_factory.interfaces.api.routers.instances import router as instance_router
from agent_factory.interfaces.api.routers.knowledge import router as knowledge_router
from agent_factory.interfaces.api.routers.prototypes import router as prototype_router

api_router = APIRouter()
api_router.include_router(prototype_router)
api_router.include_router(knowledge_router)
api_router.include_router(instance_router)
api_router.include_router(audit_router)

__all__ = ["api_router"]

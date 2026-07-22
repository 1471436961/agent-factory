"""Skill-tree registration and immutable version lookup routes."""

from http import HTTPStatus

from fastapi import APIRouter

from agent_factory.application.commands import RegisterSkillTreeCommand
from agent_factory.domain.common import SemVer, Slug
from agent_factory.domain.skills import SkillTree, SkillTreeDraft
from agent_factory.interfaces.api.contracts import RegisterSkillTreeRequest
from agent_factory.interfaces.api.dependencies import (
    ActorDep,
    ControllerDep,
    IdempotencyHeader,
    validate_command,
)

router = APIRouter(prefix="/skill-trees", tags=["skills"])


@router.post(
    "",
    response_model=SkillTree,
    status_code=HTTPStatus.CREATED,
)
async def register_skill_tree(
    body: RegisterSkillTreeRequest,
    controller: ControllerDep,
    actor: ActorDep,
    idempotency_key: IdempotencyHeader = None,
) -> SkillTree:
    command = validate_command(
        RegisterSkillTreeCommand,
        {
            "tree": SkillTreeDraft.model_validate(body.model_dump(mode="python")),
            "actor": actor,
            "idempotency_key": idempotency_key,
        },
    )
    return await controller.register_skill_tree(command)


@router.get(
    "/{tree_id}/versions/{version}",
    response_model=SkillTree,
)
async def get_skill_tree(
    tree_id: Slug,
    version: SemVer,
    controller: ControllerDep,
) -> SkillTree:
    return await controller.get_skill_tree(tree_id, version)

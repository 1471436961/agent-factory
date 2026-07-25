"""Condition rendering with explicit provenance and knowledge-fairness checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

from agent_factory.domain.common import FrozenJsonObject, canonical_json_bytes
from agent_factory.domain.models import AgentSpec
from agent_factory.domain.services.spec import checksum_agent_spec
from experiments.contracts import (
    ExperimentCondition,
    ExperimentTask,
    RenderedInvocation,
)

RENDERER_VERSION = "1.0"
_MAX_PROMPT_BYTES = 64 * 1024


class ConditionRenderingError(ValueError):
    """A condition cannot be rendered without violating experiment invariants."""


def load_manual_system_prompt(path: Path) -> tuple[str, bytes]:
    """Load a bounded UTF-8 prompt while preserving its reviewed bytes."""

    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ConditionRenderingError("manual prompt cannot be read") from exc
    if not content or len(content) > _MAX_PROMPT_BYTES:
        raise ConditionRenderingError("manual prompt size is invalid")
    try:
        prompt = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConditionRenderingError("manual prompt must be valid UTF-8") from exc
    if not prompt.strip() or "\x00" in prompt:
        raise ConditionRenderingError("manual prompt content is invalid")
    return prompt, content


def calculate_condition_bundle_checksum(manual_prompt_bytes: bytes) -> str:
    """Identify the reviewed prompt bytes and renderer implementation version."""

    payload = {
        "renderer_version": RENDERER_VERSION,
        "manual_prompt_checksum": hashlib.sha256(manual_prompt_bytes).hexdigest(),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def render_manual_invocation(
    *,
    task: ExperimentTask,
    knowledge_bytes: bytes,
    manual_system_prompt: str,
) -> RenderedInvocation:
    """Render the human-authored condition without claiming factory provenance."""

    task_input = _shared_task_input(task, knowledge_bytes)
    schema = canonical_json_bytes(task.output_schema).decode("utf-8")
    instructions = (
        f"{manual_system_prompt.rstrip()}\n\n"
        "Return exactly one JSON object matching this output schema:\n"
        f"{schema}"
    )
    return _build_rendered_invocation(
        condition=ExperimentCondition.MANUAL,
        task=task,
        instructions=instructions,
        task_input=task_input,
        output_schema=None,
        agent_spec_checksum=None,
    )


def render_factory_invocation(
    *,
    task: ExperimentTask,
    knowledge_bytes: bytes,
    agent_spec: AgentSpec,
) -> RenderedInvocation:
    """Render one controller-exported AgentSpec after validating its provenance."""

    if checksum_agent_spec(agent_spec) != agent_spec.spec_checksum:
        raise ConditionRenderingError("AgentSpec checksum is invalid")
    if agent_spec.tools:
        raise ConditionRenderingError("Writer experiment AgentSpec cannot expose tools")
    if canonical_json_bytes(agent_spec.output_schema) != canonical_json_bytes(
        task.output_schema
    ):
        raise ConditionRenderingError("AgentSpec output schema does not match task")
    expected_reference = (
        task.knowledge.knowledge_id,
        task.knowledge.version,
        task.knowledge.checksum,
    )
    references = tuple(
        (item.knowledge_id, item.version, item.checksum)
        for item in agent_spec.knowledge
    )
    if references != (expected_reference,):
        raise ConditionRenderingError(
            "AgentSpec knowledge provenance does not match task"
        )
    task_input = _shared_task_input(task, knowledge_bytes)
    return _build_rendered_invocation(
        condition=ExperimentCondition.FACTORY,
        task=task,
        instructions=agent_spec.system_prompt,
        task_input=task_input,
        output_schema=task.output_schema,
        agent_spec_checksum=agent_spec.spec_checksum,
    )


def validate_condition_pair(
    manual: RenderedInvocation,
    factory: RenderedInvocation,
) -> None:
    """Prove both conditions expose the same task and knowledge payload."""

    if manual.condition is not ExperimentCondition.MANUAL:
        raise ConditionRenderingError("manual invocation has the wrong condition")
    if factory.condition is not ExperimentCondition.FACTORY:
        raise ConditionRenderingError("factory invocation has the wrong condition")
    if manual.task_id != factory.task_id:
        raise ConditionRenderingError("condition task identities differ")
    if manual.task_input != factory.task_input:
        raise ConditionRenderingError(
            "condition-visible task or knowledge bytes differ"
        )
    if manual.knowledge_checksum != factory.knowledge_checksum:
        raise ConditionRenderingError("condition knowledge checksums differ")


def invocation_payload(invocation: RenderedInvocation) -> dict[str, object]:
    """Return exactly the provider-visible invocation fields."""

    return {
        "instructions": invocation.instructions,
        "task_input": invocation.task_input,
        "output_schema": (
            None
            if invocation.output_schema is None
            else cast(FrozenJsonObject, invocation.output_schema).to_builtin()
        ),
    }


def _shared_task_input(task: ExperimentTask, knowledge_bytes: bytes) -> str:
    try:
        knowledge = knowledge_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConditionRenderingError("knowledge must be valid UTF-8") from exc
    checksum = hashlib.sha256(knowledge_bytes).hexdigest()
    if checksum != task.knowledge.checksum:
        raise ConditionRenderingError("knowledge bytes do not match task checksum")
    return (
        f"Task:\n{task.instruction}\n\n"
        f"Reader profile:\n{task.reader_profile}\n\n"
        "Knowledge (use current facts; reject legacy values):\n"
        "---BEGIN KNOWLEDGE---\n"
        f"{knowledge}"
        "---END KNOWLEDGE---"
    )


def _build_rendered_invocation(
    *,
    condition: ExperimentCondition,
    task: ExperimentTask,
    instructions: str,
    task_input: str,
    output_schema: object,
    agent_spec_checksum: str | None,
) -> RenderedInvocation:
    visible = {
        "instructions": instructions,
        "task_input": task_input,
        "output_schema": output_schema,
    }
    prompt_hash = hashlib.sha256(canonical_json_bytes(visible)).hexdigest()
    return RenderedInvocation.model_validate(
        {
            "renderer_version": RENDERER_VERSION,
            "condition": condition,
            "task_id": task.task_id,
            "instructions": instructions,
            "task_input": task_input,
            "output_schema": output_schema,
            "knowledge_checksum": task.knowledge.checksum,
            "agent_spec_checksum": agent_spec_checksum,
            "prompt_hash": prompt_hash,
        }
    )

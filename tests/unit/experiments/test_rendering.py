"""Condition rendering and experiment-fairness tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from agent_factory.domain.enums import InjectionMode, ToolPermission
from agent_factory.domain.models import (
    AgentSpec,
    KnowledgeRef,
    PrototypeRef,
    ResolvedToolSpec,
)
from agent_factory.domain.services.spec import checksum_agent_spec
from experiments.contracts import ExperimentCondition
from experiments.loader import LoadedExperimentDataset
from experiments.rendering import (
    ConditionRenderingError,
    calculate_condition_bundle_checksum,
    invocation_payload,
    load_manual_system_prompt,
    render_factory_invocation,
    render_manual_invocation,
    validate_condition_pair,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MANUAL_PROMPT_PATH = (
    REPOSITORY_ROOT
    / "experiments"
    / "definitions"
    / "writer-v1"
    / "conditions"
    / "manual-system.txt"
)


def _agent_spec(dataset: LoadedExperimentDataset, task_index: int = 0) -> AgentSpec:
    task = dataset.tasks[task_index]
    unsigned = AgentSpec(
        instance_id=UUID("40000000-0000-0000-0000-000000000001"),
        revision=2,
        prototype=PrototypeRef(
            prototype_id="writer-prototype",
            version="1.0.0",
            checksum="a" * 64,
        ),
        agent_type="writer-agent",
        role="Technical Writer",
        system_prompt="Produce accurate documentation from the supplied knowledge.",
        tools=(),
        knowledge=(
            KnowledgeRef(
                slot_name="domain-knowledge",
                knowledge_id=task.knowledge.knowledge_id,
                version=task.knowledge.version,
                checksum=task.knowledge.checksum,
                injection_mode=InjectionMode.INLINE,
            ),
        ),
        output_schema=task.output_schema,
        generated_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        spec_checksum="0" * 64,
    )
    return unsigned.model_copy(update={"spec_checksum": checksum_agent_spec(unsigned)})


def test_condition_pair_shares_exact_task_and_knowledge_payload(
    dataset: LoadedExperimentDataset,
) -> None:
    task = dataset.tasks[0]
    key = (task.knowledge.knowledge_id, task.knowledge.version)
    knowledge = dataset.knowledge_bytes[key]
    manual_prompt, prompt_bytes = load_manual_system_prompt(MANUAL_PROMPT_PATH)

    manual = render_manual_invocation(
        task=task,
        knowledge_bytes=knowledge,
        manual_system_prompt=manual_prompt,
    )
    factory = render_factory_invocation(
        task=task,
        knowledge_bytes=knowledge,
        agent_spec=_agent_spec(dataset),
    )

    validate_condition_pair(manual, factory)
    assert manual.condition is ExperimentCondition.MANUAL
    assert factory.condition is ExperimentCondition.FACTORY
    assert manual.task_input == factory.task_input
    assert knowledge in manual.task_input.encode("utf-8")
    assert manual.output_schema is None
    assert factory.output_schema == task.output_schema
    assert invocation_payload(manual)["task_input"] == manual.task_input
    assert len(calculate_condition_bundle_checksum(prompt_bytes)) == 64


def test_rendering_rejects_knowledge_or_agentspec_provenance_mismatch(
    dataset: LoadedExperimentDataset,
) -> None:
    task = dataset.tasks[0]
    key = (task.knowledge.knowledge_id, task.knowledge.version)
    knowledge = dataset.knowledge_bytes[key]
    spec = _agent_spec(dataset)

    with pytest.raises(ConditionRenderingError, match="knowledge bytes"):
        render_manual_invocation(
            task=task,
            knowledge_bytes=knowledge + b"tampered",
            manual_system_prompt="Write accurately.",
        )

    with pytest.raises(ConditionRenderingError, match="checksum is invalid"):
        render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge,
            agent_spec=spec.model_copy(update={"spec_checksum": "b" * 64}),
        )

    other_task = dataset.tasks[1]
    with pytest.raises(ConditionRenderingError, match="output schema"):
        changed = spec.model_copy(update={"output_schema": {"type": "object"}})
        changed = changed.model_copy(
            update={"spec_checksum": checksum_agent_spec(changed)}
        )
        render_factory_invocation(
            task=other_task,
            knowledge_bytes=knowledge,
            agent_spec=changed,
        )


def test_factory_renderer_rejects_tools_and_extra_knowledge(
    dataset: LoadedExperimentDataset,
) -> None:
    task = dataset.tasks[0]
    knowledge = dataset.knowledge_bytes[
        (task.knowledge.knowledge_id, task.knowledge.version)
    ]
    spec = _agent_spec(dataset)
    tool = ResolvedToolSpec(
        name="document-search",
        version="1.0.0",
        description="Search documents.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission_tags=frozenset({ToolPermission.READ_ONLY}),
    )
    with_tool = spec.model_copy(update={"tools": (tool,)})
    with_tool = with_tool.model_copy(
        update={"spec_checksum": checksum_agent_spec(with_tool)}
    )
    with pytest.raises(ConditionRenderingError, match="cannot expose tools"):
        render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge,
            agent_spec=with_tool,
        )

    extra_reference = spec.knowledge[0].model_copy(
        update={"knowledge_id": "extra-knowledge"}
    )
    with_extra = spec.model_copy(
        update={"knowledge": (*spec.knowledge, extra_reference)}
    )
    with_extra = with_extra.model_copy(
        update={"spec_checksum": checksum_agent_spec(with_extra)}
    )
    with pytest.raises(ConditionRenderingError, match="knowledge provenance"):
        render_factory_invocation(
            task=task,
            knowledge_bytes=knowledge,
            agent_spec=with_extra,
        )


def test_rendered_invocation_rejects_prompt_hash_tampering(
    dataset: LoadedExperimentDataset,
) -> None:
    task = dataset.tasks[0]
    knowledge = dataset.knowledge_bytes[
        (task.knowledge.knowledge_id, task.knowledge.version)
    ]
    rendered = render_manual_invocation(
        task=task,
        knowledge_bytes=knowledge,
        manual_system_prompt="Write accurately.",
    )

    with pytest.raises(ValidationError, match="prompt_hash"):
        type(rendered).model_validate(
            {
                **rendered.model_dump(mode="python"),
                "prompt_hash": "f" * 64,
            }
        )


@pytest.mark.parametrize(
    ("manual_changes", "factory_changes", "message"),
    [
        ({"condition": ExperimentCondition.FACTORY}, {}, "manual invocation"),
        ({}, {"condition": ExperimentCondition.MANUAL}, "factory invocation"),
        ({"task_id": "different-task"}, {}, "task identities"),
        ({"task_input": "different input"}, {}, "task or knowledge bytes"),
        ({"knowledge_checksum": "e" * 64}, {}, "knowledge checksums"),
    ],
)
def test_condition_pair_rejects_each_fairness_mismatch(
    dataset: LoadedExperimentDataset,
    manual_changes: dict[str, object],
    factory_changes: dict[str, object],
    message: str,
) -> None:
    task = dataset.tasks[0]
    knowledge = dataset.knowledge_bytes[
        (task.knowledge.knowledge_id, task.knowledge.version)
    ]
    manual = render_manual_invocation(
        task=task,
        knowledge_bytes=knowledge,
        manual_system_prompt="Write accurately.",
    ).model_copy(update=manual_changes)
    factory = render_factory_invocation(
        task=task,
        knowledge_bytes=knowledge,
        agent_spec=_agent_spec(dataset),
    ).model_copy(update=factory_changes)

    with pytest.raises(ConditionRenderingError, match=message):
        validate_condition_pair(manual, factory)


def test_manual_prompt_loader_rejects_invalid_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(ConditionRenderingError, match="cannot be read"):
        load_manual_system_prompt(missing)

    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff")
    with pytest.raises(ConditionRenderingError, match="valid UTF-8"):
        load_manual_system_prompt(invalid)

    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(ConditionRenderingError, match="size is invalid"):
        load_manual_system_prompt(empty)

    null = tmp_path / "null.txt"
    null.write_bytes(b"valid\x00invalid")
    with pytest.raises(ConditionRenderingError, match="content is invalid"):
        load_manual_system_prompt(null)

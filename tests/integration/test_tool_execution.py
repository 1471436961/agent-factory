"""Runtime tool execution and persistence against real SQLite state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
import pytest
from tests.support import (
    EntityWriteTarget,
    FaultInjectingUnitOfWorkFactory,
    FaultPoint,
    InjectedTransactionFailure,
)

from agent_factory.application.audit import AuditEventFactory
from agent_factory.application.commands import (
    BindKnowledgeCommand,
    CloneAgentCommand,
    KnowledgeSelection,
    RegisterKnowledgeCommand,
    RegisterPrototypeCommand,
    TransitionInstanceCommand,
)
from agent_factory.application.model_gateway import (
    ModelInvocation,
    ModelToolCall,
    ModelToolResult,
    ModelTurn,
)
from agent_factory.application.queries import AuditQuery
from agent_factory.application.runtime import ResolvedRuntimeKnowledge, RunRequest
from agent_factory.application.tool_contracts import (
    RegisteredTool,
    ToolCallRequest,
    ToolCallStatus,
    ToolExecutionContext,
    ToolHandler,
)
from agent_factory.application.tool_execution import ToolExecutor
from agent_factory.container import Container, build_container
from agent_factory.domain.audit import AuditEvent
from agent_factory.domain.common import (
    FrozenModel,
    JsonObject,
    checksum_knowledge_content,
)
from agent_factory.domain.enums import (
    AuditEventType,
    Capability,
    InjectionMode,
    InstanceStatus,
    KnowledgeKind,
)
from agent_factory.domain.errors import (
    InstanceNotReadyError,
    RepositoryUnavailableError,
    ToolCallAlreadyExistsError,
    ToolContextMismatchError,
    ToolDefinitionMismatchError,
    ToolExecutionError,
    ToolInputValidationError,
    ToolNotGrantedError,
    ToolOutputValidationError,
    ToolTimeoutError,
    ToolUnavailableError,
    ToolVersionMismatchError,
)
from agent_factory.domain.models import (
    AgentDefinition,
    AgentSpec,
    DomainKnowledgeDraft,
    KnowledgeSlot,
)
from agent_factory.infrastructure.runtime import (
    DocumentSearchInput,
    DocumentSearchOutput,
    InMemoryToolRegistry,
    ModelRuntimeAdapter,
)
from agent_factory.settings import Settings

ACTOR = "runtime-test-owner"
KNOWLEDGE_CONTENT = "Agent Factory uses verified inline knowledge. SECRET-BODY-991."


@dataclass(frozen=True, slots=True)
class ToolSetup:
    settings: Settings
    container: Container
    spec: AgentSpec
    knowledge: tuple[ResolvedRuntimeKnowledge, ...]
    context: ToolExecutionContext


def _settings(tmp_path: Path, migrations_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": "runtime-test-token-that-is-at-least-32-characters",
            "auth_subject": ACTOR,
            "auth_roles": ["admin"],
        }
    )


async def _prepare(
    tmp_path: Path,
    migrations_dir: Path,
    *,
    running: bool = True,
    with_tool: bool = True,
    output_schema: JsonObject | None = None,
    runtime_target: str = "demo-runtime",
) -> ToolSetup:
    settings = _settings(tmp_path, migrations_dir)
    container = build_container(settings)
    await container.start()
    slot = KnowledgeSlot(
        name="product-docs",
        required=True,
        accepted_kinds=frozenset({KnowledgeKind.DOCUMENT}),
        min_version="1.0.0",
        injection_mode=InjectionMode.INLINE,
    )
    definition = AgentDefinition(
        agent_type="writer-agent",
        role="Technical Writer",
        system_prompt="Write using verified product documentation.",
        tools=("document-search",) if with_tool else (),
        capabilities=frozenset({Capability.WRITE}),
        output_schema=output_schema
        or {
            "type": "object",
            "required": ["title", "body"],
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
            "additionalProperties": False,
        },
        knowledge_slots=(slot,),
    )
    prototype = await container.controller.register_prototype(
        RegisterPrototypeCommand(
            prototype_id="writer-agent",
            version="1.0.0",
            definition=definition,
            publish=True,
            actor=ACTOR,
        )
    )
    knowledge = await container.controller.register_knowledge(
        RegisterKnowledgeCommand(
            knowledge=DomainKnowledgeDraft(
                knowledge_id="agent-factory-docs",
                version="1.0.0",
                name="Agent Factory Docs",
                kind=KnowledgeKind.DOCUMENT,
                content=KNOWLEDGE_CONTENT,
                checksum=checksum_knowledge_content(KNOWLEDGE_CONTENT),
            ),
            actor=ACTOR,
        )
    )
    instance = await container.controller.clone_agent(
        CloneAgentCommand(
            prototype_id=prototype.prototype_id,
            prototype_version=prototype.version,
            runtime_target=runtime_target,
            actor=ACTOR,
        )
    )
    bound = await container.controller.bind_knowledge(
        BindKnowledgeCommand(
            instance_id=instance.instance_id,
            expected_revision=instance.revision,
            selections=(
                KnowledgeSelection(
                    slot_name="product-docs",
                    knowledge_id=knowledge.knowledge_id,
                    version=knowledge.version,
                ),
            ),
            actor=ACTOR,
        )
    )
    current = bound
    if running:
        current = await container.controller.transition_instance(
            TransitionInstanceCommand(
                instance_id=bound.instance_id,
                expected_revision=bound.revision,
                target_status=InstanceStatus.RUNNING,
                reason="Start runtime tool integration test",
                actor=ACTOR,
            )
        )
    spec = await container.controller.export_spec(
        current.instance_id,
        revision=current.revision,
        actor=ACTOR,
    )
    runtime_knowledge = (
        ResolvedRuntimeKnowledge(
            slot_name="product-docs",
            knowledge_id=knowledge.knowledge_id,
            version=knowledge.version,
            checksum=knowledge.checksum,
            injection_mode=InjectionMode.INLINE,
            mime_type=knowledge.mime_type,
            content=KNOWLEDGE_CONTENT,
        ),
    )
    context = ToolExecutionContext(
        spec=spec,
        knowledge=runtime_knowledge,
        actor="demo-runtime",
        correlation_id=UUID("00000000-0000-0000-0000-000000001101"),
    )
    return ToolSetup(settings, container, spec, runtime_knowledge, context)


def _request(
    setup: ToolSetup,
    *,
    call_id: UUID | None = None,
    **updates: object,
) -> ToolCallRequest:
    payload: dict[str, object] = {
        "call_id": call_id or uuid4(),
        "task_id": UUID("00000000-0000-0000-0000-000000001102"),
        "instance_id": setup.spec.instance_id,
        "instance_revision": setup.spec.revision,
        "agent_spec_checksum": setup.spec.spec_checksum,
        "tool_name": "document-search",
        "tool_version": "1.0.0",
        "arguments": {"query": "Agent Factory", "top_k": 5},
    }
    payload.update(updates)
    return ToolCallRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_success_record_and_audit_survive_process_rebuild_without_content(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = UUID("00000000-0000-0000-0000-000000001103")
    try:
        result = await setup.container.tool_executor.execute(
            _request(setup, call_id=call_id),
            setup.context,
        )
        output = DocumentSearchOutput.model_validate(result.output)
        assert output.results[0].knowledge_id == "agent-factory-docs"
        assert result.record.status is ToolCallStatus.SUCCEEDED
        assert KNOWLEDGE_CONTENT in output.results[0].content
    finally:
        await setup.container.close()

    rebuilt = build_container(setup.settings)
    await rebuilt.start()
    try:
        async with rebuilt.uow_factory(read_only=True) as uow:
            restored = await uow.tool_calls.get(call_id)
        assert restored == result.record
        audit = await rebuilt.controller.query_audit(
            AuditQuery(
                event_types=frozenset({AuditEventType.TOOL_CALLED}),
                page_size=100,
            )
        )
        event = next(item for item in audit.items if item.entity_id == str(call_id))
        persisted = restored.model_dump_json() + event.model_dump_json()
        assert "Agent Factory" not in persisted
        assert "SECRET-BODY-991" not in persisted
        assert event.payload["arguments_hash"] == restored.arguments_hash
        assert event.payload["result_hash"] == restored.result_hash
    finally:
        await rebuilt.close()


@pytest.mark.parametrize(
    ("case", "error_type", "error_code"),
    [
        (
            "invalid-input",
            ToolInputValidationError,
            "TOOL_INPUT_VALIDATION_FAILED",
        ),
        ("wrong-version", ToolVersionMismatchError, "TOOL_VERSION_MISMATCH"),
        ("not-granted", ToolNotGrantedError, "TOOL_NOT_GRANTED"),
        ("identity", ToolContextMismatchError, "TOOL_CONTEXT_MISMATCH"),
        ("not-running", InstanceNotReadyError, "INSTANCE_NOT_READY"),
    ],
)
@pytest.mark.asyncio
async def test_pre_execution_rejections_are_persisted_and_redacted(
    tmp_path: Path,
    migrations_dir: Path,
    case: str,
    error_type: type[Exception],
    error_code: str,
) -> None:
    setup = await _prepare(
        tmp_path,
        migrations_dir,
        running=case != "not-running",
        with_tool=case != "not-granted",
    )
    call_id = uuid4()
    executions = 0

    async def counting_handler(
        payload: FrozenModel,
        context: ToolExecutionContext,
    ) -> FrozenModel:
        del payload, context
        nonlocal executions
        executions += 1
        return DocumentSearchOutput()

    executor = _executor_with_handler(setup, counting_handler)
    updates: dict[str, object] = {}
    if case == "invalid-input":
        updates["arguments"] = {
            "query": "Agent Factory",
            "api_key": "DO-NOT-PERSIST-INPUT-SECRET",
        }
    elif case == "wrong-version":
        updates["tool_version"] = "2.0.0"
    elif case == "not-granted":
        updates["tool_name"] = "document-search"
    elif case == "identity":
        updates["instance_id"] = uuid4()

    try:
        with pytest.raises(error_type):
            await executor.execute(
                _request(setup, call_id=call_id, **updates),
                setup.context,
            )
        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(call_id)
        assert record is not None
        assert record.status is ToolCallStatus.REJECTED
        assert record.error_code == error_code
        assert "DO-NOT-PERSIST" not in record.model_dump_json()
        assert executions == 0
    finally:
        await setup.container.close()


async def _timeout_handler(
    payload: FrozenModel,
    context: ToolExecutionContext,
) -> FrozenModel:
    del payload, context
    await asyncio.sleep(0.05)
    return DocumentSearchOutput()


async def _unexpected_handler(
    payload: FrozenModel,
    context: ToolExecutionContext,
) -> FrozenModel:
    del payload, context
    raise RuntimeError("DO-NOT-LOG-HANDLER-SECRET")


async def _invalid_output_handler(
    payload: FrozenModel,
    context: ToolExecutionContext,
) -> FrozenModel:
    del payload, context
    return DocumentSearchInput(query="wrong output model")


async def _cancelled_handler(
    payload: FrozenModel,
    context: ToolExecutionContext,
) -> FrozenModel:
    del payload, context
    raise asyncio.CancelledError


def _executor_with_handler(
    setup: ToolSetup,
    handler: object,
    *,
    timeout_seconds: float = 2.0,
) -> ToolExecutor:
    registered = setup.container.tool_registry.get("document-search", "1.0.0")
    assert registered is not None
    replacement = RegisteredTool(
        definition=registered.definition.model_copy(
            update={"timeout_seconds": timeout_seconds}
        ),
        input_model=registered.input_model,
        output_model=registered.output_model,
        handler=cast(ToolHandler, handler),
    )
    return ToolExecutor(
        registry=InMemoryToolRegistry((replacement,)),
        uow_factory=setup.container.uow_factory,
        clock=setup.container.clock,
        monotonic_clock=setup.container.monotonic_clock,
        audit_factory=AuditEventFactory(setup.container.id_generator),
    )


@pytest.mark.parametrize(
    ("handler", "timeout_seconds", "error_type", "status", "error_code"),
    [
        (
            _timeout_handler,
            0.001,
            ToolTimeoutError,
            ToolCallStatus.TIMED_OUT,
            "TOOL_TIMEOUT",
        ),
        (
            _unexpected_handler,
            2.0,
            ToolExecutionError,
            ToolCallStatus.FAILED,
            "TOOL_EXECUTION_FAILED",
        ),
        (
            _invalid_output_handler,
            2.0,
            ToolOutputValidationError,
            ToolCallStatus.FAILED,
            "TOOL_OUTPUT_VALIDATION_FAILED",
        ),
    ],
)
@pytest.mark.asyncio
async def test_handler_failure_matrix_is_persisted_without_exception_text(
    tmp_path: Path,
    migrations_dir: Path,
    caplog: pytest.LogCaptureFixture,
    handler: object,
    timeout_seconds: float,
    error_type: type[Exception],
    status: ToolCallStatus,
    error_code: str,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = uuid4()
    executor = _executor_with_handler(
        setup,
        handler,
        timeout_seconds=timeout_seconds,
    )
    try:
        with pytest.raises(error_type):
            await executor.execute(
                _request(setup, call_id=call_id),
                setup.context,
            )
        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(call_id)
        assert record is not None
        assert record.status is status
        assert record.error_code == error_code
        assert "DO-NOT-LOG" not in caplog.text
        assert "DO-NOT-LOG" not in record.model_dump_json()
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_external_cancellation_propagates_without_false_terminal_record(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = uuid4()
    executor = _executor_with_handler(setup, _cancelled_handler)
    try:
        with pytest.raises(asyncio.CancelledError):
            await executor.execute(
                _request(setup, call_id=call_id),
                setup.context,
            )
        async with setup.container.uow_factory(read_only=True) as uow:
            assert await uow.tool_calls.get(call_id) is None
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_duplicate_call_id_is_rejected_before_handler_runs_again(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    executions = 0

    async def counting_handler(
        payload: FrozenModel,
        context: ToolExecutionContext,
    ) -> FrozenModel:
        nonlocal executions
        executions += 1
        registered = setup.container.tool_registry.get("document-search", "1.0.0")
        assert registered is not None
        return await registered.handler(payload, context)

    executor = _executor_with_handler(setup, counting_handler)
    call_id = uuid4()
    request = _request(setup, call_id=call_id)
    try:
        await executor.execute(request, setup.context)
        with pytest.raises(ToolCallAlreadyExistsError):
            await executor.execute(request, setup.context)
        assert executions == 1
        async with setup.container.uow_factory(read_only=True) as uow:
            assert await uow.tool_calls.get(call_id) is not None
    finally:
        await setup.container.close()


@pytest.mark.parametrize(
    ("registry_case", "error_type", "error_code"),
    [
        ("unavailable", ToolUnavailableError, "TOOL_UNAVAILABLE"),
        (
            "definition-drift",
            ToolDefinitionMismatchError,
            "TOOL_DEFINITION_MISMATCH",
        ),
    ],
)
@pytest.mark.asyncio
async def test_registry_availability_and_definition_are_rechecked_at_execution(
    tmp_path: Path,
    migrations_dir: Path,
    registry_case: str,
    error_type: type[Exception],
    error_code: str,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    registered = setup.container.tool_registry.get("document-search", "1.0.0")
    assert registered is not None
    if registry_case == "unavailable":
        registry = InMemoryToolRegistry()
    else:
        registry = InMemoryToolRegistry(
            (
                RegisteredTool(
                    definition=registered.definition.model_copy(
                        update={"description": "Drifted runtime definition."}
                    ),
                    input_model=registered.input_model,
                    output_model=registered.output_model,
                    handler=registered.handler,
                ),
            )
        )
    executor = ToolExecutor(
        registry=registry,
        uow_factory=setup.container.uow_factory,
        clock=setup.container.clock,
        monotonic_clock=setup.container.monotonic_clock,
        audit_factory=AuditEventFactory(setup.container.id_generator),
    )
    call_id = uuid4()
    try:
        with pytest.raises(error_type):
            await executor.execute(
                _request(setup, call_id=call_id),
                setup.context,
            )
        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(call_id)
        assert record is not None
        assert record.status is ToolCallStatus.REJECTED
        assert record.error_code == error_code
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_repository_detects_tampered_tool_call_projection(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = uuid4()
    try:
        await setup.container.tool_executor.execute(
            _request(setup, call_id=call_id),
            setup.context,
        )
        async with aiosqlite.connect(
            setup.container.migration_runner.database_path
        ) as db:
            await db.execute(
                "UPDATE tool_call_records SET arguments_hash = ? WHERE call_id = ?",
                ("f" * 64, str(call_id)),
            )
            await db.commit()

        async with setup.container.uow_factory(read_only=True) as uow:
            with pytest.raises(RepositoryUnavailableError) as captured:
                await uow.tool_calls.get(call_id)
        assert captured.value.details["reason"] == (
            "projection-mismatch:arguments_hash"
        )
    finally:
        await setup.container.close()


class _FailingToolAuditFactory(AuditEventFactory):
    def tool_called(self, record: object) -> AuditEvent:
        del record
        raise RuntimeError("injected tool audit failure")


@pytest.mark.asyncio
async def test_record_and_audit_roll_back_together(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = uuid4()
    executor = ToolExecutor(
        registry=setup.container.tool_registry,
        uow_factory=setup.container.uow_factory,
        clock=setup.container.clock,
        monotonic_clock=setup.container.monotonic_clock,
        audit_factory=_FailingToolAuditFactory(setup.container.id_generator),
    )
    try:
        with pytest.raises(RuntimeError, match="injected tool audit failure"):
            await executor.execute(_request(setup, call_id=call_id), setup.context)
        async with setup.container.uow_factory(read_only=True) as uow:
            assert await uow.tool_calls.get(call_id) is None
        audit = await setup.container.controller.query_audit(
            AuditQuery(event_types=frozenset({AuditEventType.TOOL_CALLED}))
        )
        assert all(event.entity_id != str(call_id) for event in audit.items)
    finally:
        await setup.container.close()


@pytest.mark.parametrize(
    "fault_point",
    (
        FaultPoint.AFTER_ENTITY_WRITE,
        FaultPoint.AFTER_AUDIT_WRITE,
        FaultPoint.BEFORE_COMMIT,
    ),
)
@pytest.mark.asyncio
async def test_tool_record_and_audit_are_atomic_at_every_persistence_stage(
    fault_point: FaultPoint,
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    call_id = uuid4()
    fault_factory = FaultInjectingUnitOfWorkFactory(
        setup.container.uow_factory,
        point=fault_point,
        entity_target=EntityWriteTarget.TOOL_CALL,
    )
    executor = ToolExecutor(
        registry=setup.container.tool_registry,
        uow_factory=fault_factory,
        clock=setup.container.clock,
        monotonic_clock=setup.container.monotonic_clock,
        audit_factory=AuditEventFactory(setup.container.id_generator),
    )
    try:
        with pytest.raises(InjectedTransactionFailure) as captured:
            await executor.execute(_request(setup, call_id=call_id), setup.context)
        assert captured.value.point is fault_point

        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(call_id)
            audit = await uow.audit.query(
                AuditQuery(
                    entity_id=str(call_id),
                    event_types=frozenset({AuditEventType.TOOL_CALLED}),
                )
            )
        assert record is None
        assert audit.total == 0
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_offline_demo_runtime_completes_with_one_audited_tool_call(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(tmp_path, migrations_dir)
    try:
        result = await setup.container.demo_runtime.run(
            RunRequest(
                task_id=UUID("00000000-0000-0000-0000-000000001104"),
                spec=setup.spec,
                input="Explain Agent Factory using verified knowledge.",
                knowledge=setup.knowledge,
            )
        )
        assert result.status.value == "completed", result
        assert result.structured_output is not None
        assert result.structured_output["title"] == "Agent Factory Demo"
        assert len(result.tool_call_ids) == 1
        async with setup.container.uow_factory(read_only=True) as uow:
            assert await uow.tool_calls.get(result.tool_call_ids[0]) is not None
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_offline_demo_runtime_reports_target_and_output_schema_failures(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    invalid_schema: JsonObject = {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
        "additionalProperties": False,
    }
    setup = await _prepare(
        tmp_path,
        migrations_dir,
        output_schema=invalid_schema,
    )
    try:
        wrong_target = setup.spec.model_copy(update={"runtime_target": "other-runtime"})
        target_result = await setup.container.demo_runtime.run(
            RunRequest(
                task_id=uuid4(),
                spec=wrong_target,
                input="Write.",
                knowledge=setup.knowledge,
            )
        )
        schema_result = await setup.container.demo_runtime.run(
            RunRequest(
                task_id=uuid4(),
                spec=setup.spec,
                input="Agent Factory knowledge",
                knowledge=setup.knowledge,
            )
        )
        assert target_result.error_code == "RUNTIME_TARGET_MISMATCH"
        assert target_result.tool_call_ids == ()
        assert schema_result.error_code == "RUNTIME_OUTPUT_VALIDATION_FAILED"
        assert len(schema_result.tool_call_ids) == 1
    finally:
        await setup.container.close()


class _QueuedModelSession:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = turns
        self.received_results: list[tuple[ModelToolResult, ...]] = []

    async def next(
        self,
        tool_results: tuple[ModelToolResult, ...] = (),
    ) -> ModelTurn:
        self.received_results.append(tool_results)
        return self.turns.pop(0)


class _FakeModelGateway:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.session = _QueuedModelSession(turns)
        self.invocation: ModelInvocation | None = None

    def start(self, invocation: ModelInvocation) -> _QueuedModelSession:
        self.invocation = invocation
        return self.session


def _model_runtime(
    setup: ToolSetup, gateway: _FakeModelGateway, *, max_turns: int = 4
) -> ModelRuntimeAdapter:
    return ModelRuntimeAdapter(
        gateway=gateway,
        tool_executor=setup.container.tool_executor,
        clock=setup.container.clock,
        id_generator=setup.container.id_generator,
        max_turns=max_turns,
    )


def _model_run_request(setup: ToolSetup) -> RunRequest:
    return RunRequest(
        task_id=uuid4(),
        spec=setup.spec,
        input="Explain Agent Factory using verified knowledge.",
        knowledge=setup.knowledge,
    )


@pytest.mark.asyncio
async def test_model_runtime_executes_validated_tool_then_returns_final_output(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(
        tmp_path,
        migrations_dir,
        runtime_target="model-runtime",
    )
    gateway = _FakeModelGateway(
        [
            ModelTurn(
                model_name="fake-model",
                tool_call=ModelToolCall(
                    provider_call_id="provider-call-1",
                    name="document-search",
                    arguments={"query": "Agent Factory", "top_k": 3},
                ),
                prompt_tokens=5,
                completion_tokens=2,
            ),
            ModelTurn(
                model_name="fake-model",
                content='{"title":"Agent Factory","body":"Verified."}',
                structured_output={
                    "title": "Agent Factory",
                    "body": "Verified.",
                },
                prompt_tokens=7,
                completion_tokens=3,
            ),
        ]
    )
    try:
        result = await _model_runtime(setup, gateway).run(_model_run_request(setup))

        assert result.status.value == "completed", result
        assert result.model_name == "fake-model"
        assert result.prompt_tokens == 12
        assert result.completion_tokens == 5
        assert len(result.tool_call_ids) == 1
        assert gateway.invocation is not None
        assert gateway.invocation.tools[0].name == "document-search"
        tool_result = gateway.session.received_results[1][0]
        assert tool_result.provider_call_id == "provider-call-1"
        assert "results" in tool_result.output
        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(result.tool_call_ids[0])
        assert record is not None
        assert record.status is ToolCallStatus.SUCCEEDED
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_model_runtime_rejects_ungranted_call_and_records_attempt(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(
        tmp_path,
        migrations_dir,
        with_tool=False,
        runtime_target="model-runtime",
    )
    gateway = _FakeModelGateway(
        [
            ModelTurn(
                model_name="fake-model",
                tool_call=ModelToolCall(
                    provider_call_id="provider-call-1",
                    name="ungranted-tool",
                    arguments={},
                ),
            )
        ]
    )
    try:
        result = await _model_runtime(setup, gateway).run(_model_run_request(setup))

        assert result.error_code == "TOOL_NOT_GRANTED"
        assert len(result.tool_call_ids) == 1
        async with setup.container.uow_factory(read_only=True) as uow:
            record = await uow.tool_calls.get(result.tool_call_ids[0])
        assert record is not None
        assert record.status is ToolCallStatus.REJECTED
        assert record.error_code == "TOOL_NOT_GRANTED"
    finally:
        await setup.container.close()


@pytest.mark.asyncio
async def test_model_runtime_enforces_turn_limit_and_final_output_schema(
    tmp_path: Path,
    migrations_dir: Path,
) -> None:
    setup = await _prepare(
        tmp_path,
        migrations_dir,
        runtime_target="model-runtime",
    )
    call_turns = [
        ModelTurn(
            model_name="fake-model",
            tool_call=ModelToolCall(
                provider_call_id=f"provider-call-{number}",
                name="document-search",
                arguments={"query": "Agent Factory"},
            ),
        )
        for number in (1, 2)
    ]
    invalid_final = _FakeModelGateway(
        [
            ModelTurn(
                model_name="fake-model",
                content='{"summary":"wrong shape"}',
                structured_output={"summary": "wrong shape"},
            )
        ]
    )
    try:
        limited = await _model_runtime(
            setup,
            _FakeModelGateway(call_turns),
            max_turns=2,
        ).run(_model_run_request(setup))
        invalid = await _model_runtime(setup, invalid_final).run(
            _model_run_request(setup)
        )

        assert limited.error_code == "MODEL_TURN_LIMIT_EXCEEDED"
        assert len(limited.tool_call_ids) == 2
        assert invalid.error_code == "RUNTIME_OUTPUT_VALIDATION_FAILED"
        assert invalid.tool_call_ids == ()
    finally:
        await setup.container.close()

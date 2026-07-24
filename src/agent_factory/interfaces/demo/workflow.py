"""Checkpointed orchestration for the fixed M3.6 Writer demonstration."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from uuid import UUID, uuid5

from agent_factory.application.runtime import RunRequest, RunResult, RuntimeAdapter
from agent_factory.interfaces.demo.contracts import (
    DemoActionResult,
    DemoAuditRow,
    DemoErrorView,
    DemoPhase,
    DemoRunView,
    DemoSession,
    DemoSourceView,
)
from agent_factory.interfaces.demo.fixtures import (
    KNOWLEDGE_CHECKSUM,
    KNOWLEDGE_CONTENT,
    KNOWLEDGE_ID,
    KNOWLEDGE_SLOT,
    KNOWLEDGE_VERSION,
    PROTOTYPE_ID,
    PROTOTYPE_VERSION,
    TARGET_NODE_ID,
    TASK_INPUT,
    TOOL_NAME,
    TREE_ID,
    TREE_VERSION,
    bind_request,
    clone_request,
    evaluation_request,
    evaluation_suite_request,
    export_request,
    knowledge_request,
    promotion_request,
    prototype_request,
    review_request,
    skill_tree_request,
    transition_request,
)
from agent_factory.sdk import (
    AgentFactoryApiError,
    AgentFactoryClient,
    AgentFactoryClientClosedError,
    AgentFactoryProtocolError,
    AgentFactoryTransportError,
)

logger = logging.getLogger("agent_factory.demo")

SdkClientFactory = Callable[[], AgentFactoryClient]


class _DemoWorkflowFailure(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        correlation_id: UUID | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.correlation_id = correlation_id
        super().__init__(code)


class DemoWorkflow:
    """Drive public SDK operations and one injected RuntimeAdapter."""

    def __init__(
        self,
        *,
        client_factory: SdkClientFactory,
        runtime: RuntimeAdapter,
    ) -> None:
        self._client_factory = client_factory
        self._runtime = runtime

    async def initialize_factory(self, session: DemoSession) -> DemoActionResult:
        if session.phase is not DemoPhase.NEW:
            return self._invalid_phase(session, DemoPhase.NEW)
        current = session
        try:
            async with self._client_factory() as client:
                if not current.is_completed("api.ready"):
                    health = await client.check_readiness(
                        correlation_id=current.workflow_id
                    )
                    self._require(health.status == "ok", "API is not ready")
                    current = current.checkpoint("api.ready")

                if not current.is_completed("suite.registered"):
                    suite = await client.register_evaluation_suite(
                        evaluation_suite_request(),
                        idempotency_key=self._key(current, "suite.registered"),
                        correlation_id=current.workflow_id,
                    )
                    current = self._checkpoint_source(
                        current,
                        "suite.registered",
                        DemoSourceView(
                            source_type="evaluation-suite",
                            source_id=suite.suite_id,
                            version=suite.version,
                            checksum=suite.checksum,
                        ),
                    )
                suite_source = self._source(current, "evaluation-suite")

                if not current.is_completed("tree.registered"):
                    tree = await client.register_skill_tree(
                        skill_tree_request(suite_source.checksum),
                        idempotency_key=self._key(current, "tree.registered"),
                        correlation_id=current.workflow_id,
                    )
                    current = self._checkpoint_source(
                        current,
                        "tree.registered",
                        DemoSourceView(
                            source_type="skill-tree",
                            source_id=tree.tree_id,
                            version=tree.version,
                            checksum=tree.checksum,
                        ),
                    )
                tree_source = self._source(current, "skill-tree")

                if not current.is_completed("prototype.registered"):
                    prototype = await client.register_prototype(
                        prototype_request(tree_source.checksum),
                        idempotency_key=self._key(current, "prototype.registered"),
                        correlation_id=current.workflow_id,
                    )
                    current = self._checkpoint_source(
                        current,
                        "prototype.registered",
                        DemoSourceView(
                            source_type="prototype",
                            source_id=prototype.prototype_id,
                            version=prototype.version,
                            checksum=prototype.checksum,
                        ),
                    )

                if not current.is_completed("prototype.published"):
                    published = await client.publish_prototype(
                        PROTOTYPE_ID,
                        PROTOTYPE_VERSION,
                        idempotency_key=self._key(current, "prototype.published"),
                        correlation_id=current.workflow_id,
                    )
                    self._require(
                        published.status.value == "published",
                        "Prototype was not published",
                    )
                    current = current.checkpoint("prototype.published")

                if not current.is_completed("knowledge.registered"):
                    knowledge = await client.register_knowledge(
                        knowledge_request(),
                        idempotency_key=self._key(current, "knowledge.registered"),
                        correlation_id=current.workflow_id,
                    )
                    current = self._checkpoint_source(
                        current,
                        "knowledge.registered",
                        DemoSourceView(
                            source_type="knowledge",
                            source_id=knowledge.knowledge_id,
                            version=knowledge.version,
                            checksum=knowledge.checksum,
                        ),
                    )

                if not current.is_completed("instance.cloned"):
                    instance = await client.clone_agent(
                        PROTOTYPE_ID,
                        PROTOTYPE_VERSION,
                        clone_request(),
                        idempotency_key=self._key(current, "instance.cloned"),
                        correlation_id=current.workflow_id,
                    )
                    self._require_instance(
                        instance.revision, instance.status.value, 1, "created"
                    )
                    current = current.checkpoint(
                        "instance.cloned",
                        instance_id=instance.instance_id,
                        revision=instance.revision,
                    )
                instance_id = self._instance_id(current)

                if not current.is_completed("unbound-spec.rejected"):
                    try:
                        await client.export_spec(
                            instance_id,
                            export_request(1),
                            correlation_id=current.workflow_id,
                        )
                    except AgentFactoryApiError as exc:
                        if exc.code != "MISSING_KNOWLEDGE_BINDING":
                            raise
                    else:
                        raise _DemoWorkflowFailure(
                            "DEMO_INVARIANT_FAILED",
                            "Unbound AgentSpec export was not rejected",
                            correlation_id=current.workflow_id,
                        )
                    current = current.checkpoint("unbound-spec.rejected")

                if not current.is_completed("knowledge.bound"):
                    bound = await client.bind_knowledge(
                        instance_id,
                        bind_request(1),
                        idempotency_key=self._key(current, "knowledge.bound"),
                        correlation_id=current.workflow_id,
                    )
                    self._require_instance(
                        bound.revision, bound.status.value, 2, "created"
                    )
                    current = current.checkpoint(
                        "knowledge.bound",
                        revision=bound.revision,
                    )

                if not current.is_completed("spec.revision-2"):
                    spec = await client.export_spec(
                        instance_id,
                        export_request(2),
                        correlation_id=current.workflow_id,
                    )
                    self._verify_spec(
                        current,
                        spec.model_dump(mode="json"),
                        expected_revision=2,
                    )
                    current = current.checkpoint(
                        "spec.revision-2",
                        spec_json=spec.model_dump_json(),
                    )

                if not current.is_completed("instance.running"):
                    running = await client.transition_instance(
                        instance_id,
                        transition_request(
                            2, "running", "Start the fixed Writer task."
                        ),
                        idempotency_key=self._key(current, "instance.running"),
                        correlation_id=current.workflow_id,
                    )
                    self._require_instance(
                        running.revision, running.status.value, 3, "running"
                    )
                    current = current.checkpoint(
                        "instance.running",
                        revision=running.revision,
                    )

                if not current.is_completed("spec.revision-3"):
                    spec = await client.export_spec(
                        instance_id,
                        export_request(3),
                        correlation_id=current.workflow_id,
                    )
                    self._verify_spec(
                        current,
                        spec.model_dump(mode="json"),
                        expected_revision=3,
                    )
                    current = current.checkpoint(
                        "spec.revision-3",
                        spec_json=spec.model_dump_json(),
                    )

                if not current.is_completed("audit.after-initialize"):
                    current = current.checkpoint(
                        "audit.after-initialize",
                        audit_rows=await self._audit_rows(client, current.workflow_id),
                    )
                current = current.checkpoint(
                    "phase.ready-to-run",
                    phase=DemoPhase.READY_TO_RUN,
                )
        except Exception as exc:
            return self._failure(current, exc)
        return DemoActionResult(session=current)

    async def run_and_evaluate(self, session: DemoSession) -> DemoActionResult:
        if session.phase is not DemoPhase.READY_TO_RUN:
            return self._invalid_phase(session, DemoPhase.READY_TO_RUN)
        current = session
        try:
            if not current.is_completed("runtime.executed"):
                request = self._run_request(current)
                result = await self._runtime.run(request)
                current = current.checkpoint(
                    "runtime.executed",
                    run_result_json=result.model_dump_json(),
                    run_view=self._run_view(result),
                )
            run_result = self._run_result(current)
            if run_result.status.value != "completed":
                raise _DemoWorkflowFailure(
                    run_result.error_code or "RUNTIME_EXECUTION_FAILED",
                    "The offline Runtime did not complete the fixed task",
                    correlation_id=run_result.task_id,
                )

            async with self._client_factory() as client:
                instance_id = self._instance_id(current)
                if not current.is_completed("instance.waiting"):
                    waiting = await client.transition_instance(
                        instance_id,
                        transition_request(
                            3, "waiting", "The fixed Writer task completed."
                        ),
                        idempotency_key=self._key(current, "instance.waiting"),
                        correlation_id=current.workflow_id,
                    )
                    self._require_instance(
                        waiting.revision, waiting.status.value, 4, "waiting"
                    )
                    current = current.checkpoint(
                        "instance.waiting",
                        revision=waiting.revision,
                    )

                if not current.is_completed("spec.revision-4"):
                    spec = await client.export_spec(
                        instance_id,
                        export_request(4),
                        correlation_id=current.workflow_id,
                    )
                    self._verify_spec(
                        current,
                        spec.model_dump(mode="json"),
                        expected_revision=4,
                    )
                    current = current.checkpoint(
                        "spec.revision-4",
                        spec_json=spec.model_dump_json(),
                    )

                if not current.is_completed("evaluation.completed"):
                    suite_source = self._source(current, "evaluation-suite")
                    report = await client.evaluate_instance(
                        instance_id,
                        evaluation_request(
                            expected_revision=4,
                            suite_checksum=suite_source.checksum,
                            output_text=run_result.content,
                            structured_output=run_result.model_dump(mode="json")[
                                "structured_output"
                            ],
                            tool_was_called=bool(run_result.tool_call_ids),
                        ),
                        idempotency_key=self._key(current, "evaluation.completed"),
                        correlation_id=current.workflow_id,
                    )
                    self._require(
                        report.decision.value == "review-required",
                        "Evaluation did not require explicit human review",
                    )
                    current = current.checkpoint(
                        "evaluation.completed",
                        report_id=report.report_id,
                    )

                if not current.is_completed("audit.after-evaluation"):
                    current = current.checkpoint(
                        "audit.after-evaluation",
                        audit_rows=await self._audit_rows(client, current.workflow_id),
                    )
                current = current.checkpoint(
                    "phase.awaiting-review",
                    phase=DemoPhase.AWAITING_REVIEW,
                )
        except Exception as exc:
            return self._failure(current, exc)
        return DemoActionResult(session=current)

    async def approve_and_promote(self, session: DemoSession) -> DemoActionResult:
        if session.phase is not DemoPhase.AWAITING_REVIEW:
            return self._invalid_phase(session, DemoPhase.AWAITING_REVIEW)
        current = session
        try:
            report_id = self._report_id(current)
            async with self._client_factory() as client:
                if not current.is_completed("evaluation.reviewed"):
                    review = await client.review_evaluation(
                        report_id,
                        review_request(),
                        idempotency_key=self._key(current, "evaluation.reviewed"),
                        correlation_id=current.workflow_id,
                    )
                    self._require(
                        review.decision.value == "approved",
                        "Evaluation review was not approved",
                    )
                    current = current.checkpoint(
                        "evaluation.reviewed",
                        review_id=review.review_id,
                    )

                if not current.is_completed("skill.promoted"):
                    promoted = await client.promote_agent(
                        self._instance_id(current),
                        promotion_request(
                            expected_revision=4,
                            report_id=report_id,
                            review_id=self._review_id(current),
                        ),
                        idempotency_key=self._key(current, "skill.promoted"),
                        correlation_id=current.workflow_id,
                    )
                    self._require_instance(
                        promoted.revision,
                        promoted.status.value,
                        5,
                        "waiting",
                    )
                    active_nodes = tuple(sorted(promoted.active_skill_nodes))
                    self._require(
                        active_nodes == (TARGET_NODE_ID,),
                        "Promotion produced unexpected active skill nodes",
                    )
                    current = current.checkpoint(
                        "skill.promoted",
                        revision=promoted.revision,
                        active_nodes=active_nodes,
                    )

                if not current.is_completed("audit.after-promotion"):
                    current = current.checkpoint(
                        "audit.after-promotion",
                        audit_rows=await self._audit_rows(client, current.workflow_id),
                    )
                current = current.checkpoint(
                    "phase.promoted",
                    phase=DemoPhase.PROMOTED,
                )
        except Exception as exc:
            return self._failure(current, exc)
        return DemoActionResult(session=current)

    @staticmethod
    def _key(session: DemoSession, operation: str) -> str:
        return f"demo:{session.workflow_id}:{operation}"

    @staticmethod
    def _checkpoint_source(
        session: DemoSession,
        operation: str,
        source: DemoSourceView,
    ) -> DemoSession:
        return session.replace_source(source).checkpoint(operation)

    @staticmethod
    def _source(session: DemoSession, source_type: str) -> DemoSourceView:
        source = session.source(source_type)
        if source is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                f"Missing {source_type} checkpoint",
                correlation_id=session.workflow_id,
            )
        return source

    @staticmethod
    def _instance_id(session: DemoSession) -> UUID:
        if session.instance_id is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                "Missing instance checkpoint",
                correlation_id=session.workflow_id,
            )
        return session.instance_id

    @staticmethod
    def _report_id(session: DemoSession) -> UUID:
        if session.report_id is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                "Missing evaluation report checkpoint",
                correlation_id=session.workflow_id,
            )
        return session.report_id

    @staticmethod
    def _review_id(session: DemoSession) -> UUID:
        if session.review_id is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                "Missing evaluation review checkpoint",
                correlation_id=session.workflow_id,
            )
        return session.review_id

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise _DemoWorkflowFailure("DEMO_INVARIANT_FAILED", message)

    @classmethod
    def _require_instance(
        cls,
        revision: int,
        status: str,
        expected_revision: int,
        expected_status: str,
    ) -> None:
        cls._require(
            revision == expected_revision and status == expected_status,
            "Instance revision or lifecycle status violated the demo contract",
        )

    @classmethod
    def _verify_spec(
        cls,
        session: DemoSession,
        spec: Mapping[str, object],
        *,
        expected_revision: int,
    ) -> None:
        prototype_source = cls._source(session, "prototype")
        knowledge_source = cls._source(session, "knowledge")
        tree_source = cls._source(session, "skill-tree")
        revision = spec.get("revision")
        schema_version = spec.get("schema_version")
        prototype = cls._mapping(spec.get("prototype"), "prototype")
        knowledge = cls._sequence(spec.get("knowledge"), "knowledge")
        skill_tree = cls._mapping(spec.get("skill_tree"), "skill_tree")
        tools = cls._sequence(spec.get("tools"), "tools")
        cls._require(revision == expected_revision, "AgentSpec revision mismatch")
        cls._require(schema_version == "1.1", "Writer AgentSpec must use schema 1.1")
        cls._require(
            prototype.get("prototype_id") == PROTOTYPE_ID
            and prototype.get("version") == PROTOTYPE_VERSION
            and prototype.get("checksum") == prototype_source.checksum,
            "AgentSpec prototype provenance mismatch",
        )
        cls._require(
            len(knowledge) == 1,
            "AgentSpec knowledge provenance mismatch",
        )
        knowledge_ref = cls._mapping(knowledge[0], "knowledge reference")
        cls._require(
            knowledge_ref.get("slot_name") == KNOWLEDGE_SLOT
            and knowledge_ref.get("knowledge_id") == KNOWLEDGE_ID
            and knowledge_ref.get("version") == KNOWLEDGE_VERSION
            and knowledge_ref.get("checksum") == knowledge_source.checksum,
            "AgentSpec knowledge provenance mismatch",
        )
        cls._require(
            skill_tree.get("tree_id") == TREE_ID
            and skill_tree.get("version") == TREE_VERSION
            and skill_tree.get("checksum") == tree_source.checksum,
            "AgentSpec skill-tree provenance mismatch",
        )
        cls._require(
            tuple(cls._mapping(tool, "tool reference").get("name") for tool in tools)
            == (TOOL_NAME,),
            "AgentSpec tool grant mismatch",
        )

    @staticmethod
    def _mapping(value: object, field: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise _DemoWorkflowFailure(
                "DEMO_INVARIANT_FAILED",
                f"AgentSpec {field} is not an object",
            )
        return value

    @staticmethod
    def _sequence(value: object, field: str) -> Sequence[object]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise _DemoWorkflowFailure(
                "DEMO_INVARIANT_FAILED",
                f"AgentSpec {field} is not an array",
            )
        return value

    @staticmethod
    def _run_request(session: DemoSession) -> RunRequest:
        if session.spec_json is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                "Missing runnable AgentSpec checkpoint",
                correlation_id=session.workflow_id,
            )
        task_id = uuid5(session.workflow_id, "m3.6-runtime-task")
        return RunRequest.model_validate(
            {
                "task_id": task_id,
                "spec": json.loads(session.spec_json),
                "input": TASK_INPUT,
                "knowledge": [
                    {
                        "slot_name": KNOWLEDGE_SLOT,
                        "knowledge_id": KNOWLEDGE_ID,
                        "version": KNOWLEDGE_VERSION,
                        "checksum": KNOWLEDGE_CHECKSUM,
                        "injection_mode": "inline",
                        "mime_type": "text/plain",
                        "content": KNOWLEDGE_CONTENT,
                    }
                ],
                "metadata": {"demo": "m3.6", "workflow_id": str(session.workflow_id)},
            }
        )

    @staticmethod
    def _run_result(session: DemoSession) -> RunResult:
        if session.run_result_json is None:
            raise _DemoWorkflowFailure(
                "DEMO_STATE_INVALID",
                "Missing Runtime result checkpoint",
                correlation_id=session.workflow_id,
            )
        return RunResult.model_validate_json(session.run_result_json)

    @staticmethod
    def _run_view(result: RunResult) -> DemoRunView:
        preview = result.content.partition("\n\nVerified knowledge:")[0]
        structured_keys = (
            tuple(sorted(result.structured_output.keys()))
            if result.structured_output is not None
            else ()
        )
        return DemoRunView(
            task_id=result.task_id,
            status=result.status.value,
            instance_revision=result.instance_revision,
            agent_spec_checksum=result.agent_spec_checksum,
            runtime_name=result.runtime_name,
            tool_call_count=len(result.tool_call_ids),
            content_preview=preview[:1_000],
            structured_keys=structured_keys,
        )

    @staticmethod
    async def _audit_rows(
        client: AgentFactoryClient,
        correlation_id: UUID,
    ) -> tuple[DemoAuditRow, ...]:
        page = await client.query_audit(
            page=1,
            page_size=100,
            correlation_id=correlation_id,
        )
        rows = (
            DemoAuditRow(
                created_at=event.created_at,
                event_type=event.event_type.value,
                entity_type=event.entity_type.value,
                entity_id=event.entity_id,
                entity_revision=event.entity_revision,
                correlation_id=event.correlation_id,
            )
            for event in page.items
        )
        return tuple(
            sorted(
                rows, key=lambda row: (row.created_at, row.event_type, row.entity_id)
            )
        )

    @staticmethod
    def _invalid_phase(
        session: DemoSession,
        required: DemoPhase,
    ) -> DemoActionResult:
        return DemoActionResult(
            session=session,
            error=DemoErrorView(
                code="DEMO_INVALID_PHASE",
                message=(
                    f"This action requires phase {required.value}; "
                    f"current phase is {session.phase.value}"
                ),
                correlation_id=session.workflow_id,
            ),
        )

    @staticmethod
    def _failure(session: DemoSession, exc: Exception) -> DemoActionResult:
        if isinstance(exc, AgentFactoryApiError):
            error = DemoErrorView(
                code=exc.code,
                message=exc.message,
                correlation_id=exc.correlation_id,
            )
        elif isinstance(exc, AgentFactoryTransportError):
            error = DemoErrorView(
                code="DEMO_API_UNAVAILABLE",
                message="Agent Factory API could not be reached",
                correlation_id=exc.correlation_id,
            )
        elif isinstance(exc, AgentFactoryProtocolError):
            error = DemoErrorView(
                code="DEMO_API_PROTOCOL_ERROR",
                message="Agent Factory API returned an invalid response",
                correlation_id=exc.correlation_id,
            )
        elif isinstance(exc, AgentFactoryClientClosedError):
            error = DemoErrorView(
                code="DEMO_SDK_CLOSED",
                message="The Agent Factory SDK client is closed",
                correlation_id=session.workflow_id,
            )
        elif isinstance(exc, _DemoWorkflowFailure):
            error = DemoErrorView(
                code=exc.code,
                message=exc.message,
                correlation_id=exc.correlation_id or session.workflow_id,
            )
        else:
            logger.error(
                "demo_workflow_failed",
                extra={
                    "workflow_id": str(session.workflow_id),
                    "exception_type": type(exc).__name__,
                },
            )
            error = DemoErrorView(
                code="DEMO_INTERNAL_ERROR",
                message="The demo workflow failed unexpectedly",
                correlation_id=session.workflow_id,
            )
        return DemoActionResult(session=session, error=error)

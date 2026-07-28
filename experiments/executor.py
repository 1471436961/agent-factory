"""Sequential, recoverable M5 executor over immutable local artifacts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from agent_factory.application.ports import Clock
from agent_factory.infrastructure.system import SystemClock
from experiments.artifacts import ArtifactConflictError, ArtifactStore
from experiments.contracts import (
    AttemptCompletion,
    AttemptIntent,
    AttemptStatus,
    ExecutionManifest,
    ExecutionPlan,
    ExecutionPlanItem,
    ExperimentCondition,
    ExperimentRun,
    ExperimentRunRequest,
    ExperimentTask,
    RenderedInvocation,
    RunAttempt,
    RunStatus,
)
from experiments.gateway import (
    ExperimentGateway,
    GatewayFailure,
    GatewayFailureKind,
    GatewayRequest,
    GatewaySuccess,
)
from experiments.loader import ExperimentFixtureError, LoadedExperimentDataset
from experiments.planning import (
    validate_execution_manifest,
    validate_execution_plan,
)
from experiments.rendering import invocation_payload


class ExecutionAbortedError(RuntimeError):
    """Execution identity or recovery evidence is inconsistent."""


class Sleeper(Protocol):
    async def sleep(self, seconds: float) -> None:
        """Wait before one retry attempt."""


class InvocationProvider(Protocol):
    def render(
        self,
        task: ExperimentTask,
        condition: ExperimentCondition,
    ) -> RenderedInvocation:
        """Render one immutable provider invocation for a plan coordinate."""


class AsyncioSleeper:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(slots=True)
class _BudgetLedger:
    provider_requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def reserve(self, intent: AttemptIntent) -> None:
        self.provider_requests += 1
        self.prompt_tokens += intent.reserved_prompt_tokens
        self.completion_tokens += intent.reserved_completion_tokens


class ExperimentExecutor:
    """Execute a fixed plan without overwriting requests, attempts, or runs."""

    def __init__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        manifest: ExecutionManifest,
        invocation_provider: InvocationProvider,
        gateway: ExperimentGateway,
        store: ArtifactStore,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
        allow_live: bool = False,
    ) -> None:
        self._dataset = dataset
        self._plan = plan
        self._manifest = manifest
        self._invocation_provider = invocation_provider
        self._gateway = gateway
        self._store = store
        self._clock = clock or SystemClock()
        self._sleeper = sleeper or AsyncioSleeper()
        self._allow_live = allow_live
        self._tasks = {task.task_id: task for task in dataset.tasks}

    async def execute(
        self,
        *,
        max_items: int | None = None,
    ) -> tuple[ExperimentRun, ...]:
        """Execute or recover every plan item in immutable plan order."""

        self._validate_before_execution()
        if max_items is not None and not 1 <= max_items <= len(self._plan.items):
            raise ValueError("max_items must select at least one plan item")
        ledger = self._rebuild_budget_ledger()
        runs: list[ExperimentRun] = []
        items = self._plan.items if max_items is None else self._plan.items[:max_items]
        for item in items:
            terminal_path = self._terminal_path(item)
            if self._store.exists(terminal_path):
                if not self._store.exists(self._request_path(item)):
                    raise ExecutionAbortedError("terminal run is missing its request")
                request = self._load_or_create_request(item)
                attempts = self._recover_attempts(item, request)
                run = self._store.read_model(terminal_path, ExperimentRun)
                self._validate_terminal(run, item, request, attempts)
                runs.append(run)
                continue
            request = self._load_or_create_request(item)
            run = await self._execute_item(item, request, ledger)
            runs.append(run)
        return tuple(runs)

    def _validate_before_execution(self) -> None:
        try:
            validate_execution_plan(self._plan, self._dataset)
            validate_execution_manifest(self._manifest, self._dataset, self._plan)
        except ExperimentFixtureError as exc:
            raise ExecutionAbortedError(str(exc)) from exc
        if self._gateway.is_live and not self._allow_live:
            raise ExecutionAbortedError("live gateway requires explicit approval")
        self._store.write_model_once(
            self._manifest_path(),
            self._manifest,
        )

    def _rebuild_budget_ledger(self) -> _BudgetLedger:
        ledger = _BudgetLedger()
        for item in self._plan.items:
            for attempt_number in range(1, self._manifest.generation.max_attempts + 1):
                path = self._intent_path(item, attempt_number)
                if self._store.exists(path):
                    intent = self._store.read_model(path, AttemptIntent)
                    self._validate_intent(intent, item, attempt_number)
                    ledger.reserve(intent)
        return ledger

    def _load_or_create_request(
        self,
        item: ExecutionPlanItem,
    ) -> ExperimentRunRequest:
        task = self._tasks[item.task_id]
        rendered = self._invocation_provider.render(task, item.condition)
        if rendered.task_id != item.task_id or rendered.condition is not item.condition:
            raise ExecutionAbortedError(
                "invocation provider returned another coordinate"
            )
        invocation = invocation_payload(rendered)
        path = self._request_path(item)
        if self._store.exists(path):
            existing = self._store.read_model(path, ExperimentRunRequest)
            expected = self._request_from_rendered(
                item,
                rendered,
                invocation,
                started_at=existing.started_at,
            )
            if existing != expected:
                raise ArtifactConflictError("stored request no longer matches renderer")
            return existing
        request = self._request_from_rendered(
            item,
            rendered,
            invocation,
            started_at=self._clock.now(),
        )
        self._store.write_model_once(path, request)
        return request

    async def _execute_item(
        self,
        item: ExecutionPlanItem,
        request: ExperimentRunRequest,
        ledger: _BudgetLedger,
    ) -> ExperimentRun:
        attempts = self._recover_attempts(item, request)
        while True:
            if attempts and (
                attempts[-1].status is AttemptStatus.SUCCEEDED
                or not attempts[-1].retryable
                or len(attempts) >= request.generation.max_attempts
            ):
                return self._publish_terminal(item, request, attempts)

            attempt_number = len(attempts) + 1
            if not self._has_budget_for_attempt(ledger):
                if attempts:
                    return self._publish_terminal(item, request, attempts)
                return self._publish_budget_stopped(item, request)

            backoff_seconds = (
                0.0 if attempt_number == 1 else 2.0 ** (attempt_number - 2)
            )
            if backoff_seconds:
                await self._sleeper.sleep(backoff_seconds)
            intent = AttemptIntent(
                run_id=item.run_id,
                manifest_checksum=self._manifest.manifest_checksum,
                attempt_number=attempt_number,
                prompt_hash=request.prompt_hash,
                reserved_prompt_tokens=(
                    self._manifest.limits.prompt_tokens_per_attempt_upper_bound
                ),
                reserved_completion_tokens=request.generation.max_output_tokens,
                backoff_seconds=backoff_seconds,
                started_at=self._now_not_before(
                    request.started_at,
                    *(attempt.completed_at for attempt in attempts[-1:]),
                ),
            )
            self._store.write_model_once(
                self._intent_path(item, attempt_number),
                intent,
            )
            ledger.reserve(intent)
            outcome = await self._gateway.generate(
                GatewayRequest(
                    run_id=item.run_id,
                    attempt_number=attempt_number,
                    generation=request.generation,
                    invocation=request.invocation,
                    expected_output_schema=self._tasks[item.task_id].output_schema,
                    prompt_hash=request.prompt_hash,
                )
            )
            attempt = self._complete_attempt(intent, outcome)
            self._store.write_model_once(
                self._completion_path(item, attempt_number),
                AttemptCompletion(
                    run_id=item.run_id,
                    manifest_checksum=self._manifest.manifest_checksum,
                    attempt=attempt,
                ),
            )
            attempts.append(attempt)

    def _recover_attempts(
        self,
        item: ExecutionPlanItem,
        request: ExperimentRunRequest,
    ) -> list[RunAttempt]:
        attempts: list[RunAttempt] = []
        found_gap = False
        for attempt_number in range(1, request.generation.max_attempts + 1):
            intent_path = self._intent_path(item, attempt_number)
            completion_path = self._completion_path(item, attempt_number)
            intent_exists = self._store.exists(intent_path)
            completion_exists = self._store.exists(completion_path)
            if not intent_exists and not completion_exists:
                found_gap = True
                continue
            if found_gap or not intent_exists:
                raise ExecutionAbortedError("attempt journal is non-contiguous")
            intent = self._store.read_model(intent_path, AttemptIntent)
            self._validate_intent(intent, item, attempt_number)
            if (
                intent.prompt_hash != request.prompt_hash
                or intent.reserved_prompt_tokens
                != self._manifest.limits.prompt_tokens_per_attempt_upper_bound
                or intent.reserved_completion_tokens
                != self._manifest.generation.max_output_tokens
            ):
                raise ExecutionAbortedError("attempt intent reservation mismatch")
            if completion_exists:
                completion = self._store.read_model(
                    completion_path,
                    AttemptCompletion,
                )
                self._validate_completion(completion, item, attempt_number)
                if completion.attempt.started_at != intent.started_at:
                    raise ExecutionAbortedError("attempt journal timestamps mismatch")
                attempts.append(completion.attempt)
                continue
            unknown = RunAttempt(
                attempt_number=attempt_number,
                status=AttemptStatus.PROVIDER_FAILED,
                error_code="RESULT_UNKNOWN_AFTER_INTERRUPTION",
                retryable=True,
                started_at=intent.started_at,
                completed_at=self._now_not_before(intent.started_at),
            )
            self._store.write_model_once(
                completion_path,
                AttemptCompletion(
                    run_id=item.run_id,
                    manifest_checksum=self._manifest.manifest_checksum,
                    attempt=unknown,
                ),
            )
            attempts.append(unknown)
        return attempts

    def _complete_attempt(
        self,
        intent: AttemptIntent,
        outcome: GatewaySuccess | GatewayFailure,
    ) -> RunAttempt:
        if isinstance(outcome, GatewaySuccess):
            return RunAttempt(
                attempt_number=intent.attempt_number,
                status=AttemptStatus.SUCCEEDED,
                provider_request_id=outcome.provider_request_id,
                response=outcome.raw_response,
                output_text=outcome.output_text,
                structured_output=outcome.structured_output,
                prompt_tokens=outcome.prompt_tokens,
                completion_tokens=outcome.completion_tokens,
                started_at=intent.started_at,
                completed_at=self._now_not_before(intent.started_at),
            )
        status = {
            GatewayFailureKind.TIMED_OUT: AttemptStatus.TIMED_OUT,
            GatewayFailureKind.FILTERED: AttemptStatus.FILTERED,
            GatewayFailureKind.INVALID_RESPONSE: AttemptStatus.INVALID_RESPONSE,
        }.get(outcome.kind, AttemptStatus.PROVIDER_FAILED)
        retryable = outcome.kind in {
            GatewayFailureKind.NETWORK,
            GatewayFailureKind.RATE_LIMITED,
            GatewayFailureKind.SERVER_ERROR,
            GatewayFailureKind.TIMED_OUT,
        }
        return RunAttempt(
            attempt_number=intent.attempt_number,
            status=status,
            provider_request_id=outcome.provider_request_id,
            error_response=outcome.raw_response,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            error_code=outcome.error_code,
            retryable=retryable,
            started_at=intent.started_at,
            completed_at=self._now_not_before(intent.started_at),
        )

    def _publish_terminal(
        self,
        item: ExecutionPlanItem,
        request: ExperimentRunRequest,
        attempts: list[RunAttempt],
    ) -> ExperimentRun:
        last = attempts[-1]
        status = RunStatus(last.status.value)
        run = ExperimentRun(
            run_id=item.run_id,
            experiment_id=request.experiment_id,
            manifest_checksum=request.manifest_checksum,
            plan_checksum=request.plan_checksum,
            condition=request.condition,
            task_id=request.task_id,
            repetition=request.repetition,
            execution_order=request.execution_order,
            generation=request.generation,
            invocation=request.invocation,
            prompt_hash=request.prompt_hash,
            knowledge_checksum=request.knowledge_checksum,
            agent_spec_checksum=request.agent_spec_checksum,
            status=status,
            attempts=tuple(attempts),
            output_text=last.output_text,
            structured_output=last.structured_output,
            started_at=request.started_at,
            completed_at=self._now_not_before(
                request.started_at,
                last.completed_at,
            ),
        )
        self._store.write_model_once(self._terminal_path(item), run)
        return run

    def _publish_budget_stopped(
        self,
        item: ExecutionPlanItem,
        request: ExperimentRunRequest,
    ) -> ExperimentRun:
        run = ExperimentRun(
            run_id=item.run_id,
            experiment_id=request.experiment_id,
            manifest_checksum=request.manifest_checksum,
            plan_checksum=request.plan_checksum,
            condition=request.condition,
            task_id=request.task_id,
            repetition=request.repetition,
            execution_order=request.execution_order,
            generation=request.generation,
            invocation=request.invocation,
            prompt_hash=request.prompt_hash,
            knowledge_checksum=request.knowledge_checksum,
            agent_spec_checksum=request.agent_spec_checksum,
            status=RunStatus.BUDGET_STOPPED,
            started_at=request.started_at,
            completed_at=self._now_not_before(request.started_at),
        )
        self._store.write_model_once(self._terminal_path(item), run)
        return run

    def _has_budget_for_attempt(self, ledger: _BudgetLedger) -> bool:
        limits = self._manifest.limits
        generation = self._manifest.generation
        return (
            ledger.provider_requests + 1 <= limits.max_provider_requests
            and ledger.prompt_tokens + limits.prompt_tokens_per_attempt_upper_bound
            <= limits.max_prompt_tokens
            and ledger.completion_tokens + generation.max_output_tokens
            <= limits.max_completion_tokens
        )

    def _request_from_rendered(
        self,
        item: ExecutionPlanItem,
        rendered: RenderedInvocation,
        invocation: dict[str, object],
        *,
        started_at: datetime,
    ) -> ExperimentRunRequest:
        return ExperimentRunRequest(
            run_id=item.run_id,
            experiment_id=self._manifest.experiment_id,
            manifest_checksum=self._manifest.manifest_checksum,
            plan_checksum=self._plan.plan_checksum,
            condition=item.condition,
            task_id=item.task_id,
            repetition=item.repetition,
            execution_order=item.execution_order,
            generation=self._manifest.generation,
            invocation=invocation,
            prompt_hash=rendered.prompt_hash,
            knowledge_checksum=rendered.knowledge_checksum,
            agent_spec_checksum=rendered.agent_spec_checksum,
            started_at=started_at,
        )

    def _validate_terminal(
        self,
        run: ExperimentRun,
        item: ExecutionPlanItem,
        request: ExperimentRunRequest,
        attempts: list[RunAttempt],
    ) -> None:
        identity = (
            run.run_id,
            run.experiment_id,
            run.manifest_checksum,
            run.plan_checksum,
            run.condition,
            run.task_id,
            run.repetition,
            run.execution_order,
        )
        expected = (
            item.run_id,
            self._manifest.experiment_id,
            self._manifest.manifest_checksum,
            self._plan.plan_checksum,
            item.condition,
            item.task_id,
            item.repetition,
            item.execution_order,
        )
        if (
            identity != expected
            or run.generation != self._manifest.generation
            or run.invocation != request.invocation
            or run.prompt_hash != request.prompt_hash
            or run.knowledge_checksum != request.knowledge_checksum
            or run.agent_spec_checksum != request.agent_spec_checksum
            or run.started_at != request.started_at
            or run.attempts != tuple(attempts)
        ):
            raise ExecutionAbortedError("terminal run identity mismatch")

    def _validate_intent(
        self,
        intent: AttemptIntent,
        item: ExecutionPlanItem,
        attempt_number: int,
    ) -> None:
        if (
            intent.run_id != item.run_id
            or intent.manifest_checksum != self._manifest.manifest_checksum
            or intent.attempt_number != attempt_number
        ):
            raise ExecutionAbortedError("attempt intent identity mismatch")

    def _validate_completion(
        self,
        completion: AttemptCompletion,
        item: ExecutionPlanItem,
        attempt_number: int,
    ) -> None:
        if (
            completion.run_id != item.run_id
            or completion.manifest_checksum != self._manifest.manifest_checksum
            or completion.attempt.attempt_number != attempt_number
        ):
            raise ExecutionAbortedError("attempt completion identity mismatch")

    def _base_path(self) -> str:
        return self._manifest.experiment_id

    def _request_path(self, item: ExecutionPlanItem) -> str:
        return f"{self._base_path()}/requests/{item.run_id}.json"

    def _manifest_path(self) -> str:
        return f"{self._base_path()}/execution-manifest.json"

    def _intent_path(self, item: ExecutionPlanItem, attempt_number: int) -> str:
        return (
            f"{self._base_path()}/attempts/{item.run_id}/"
            f"{attempt_number:03d}-started.json"
        )

    def _completion_path(self, item: ExecutionPlanItem, attempt_number: int) -> str:
        return (
            f"{self._base_path()}/attempts/{item.run_id}/"
            f"{attempt_number:03d}-completed.json"
        )

    def _terminal_path(self, item: ExecutionPlanItem) -> str:
        return f"{self._base_path()}/terminal/{item.run_id}.json"

    def _now_not_before(self, *earliest: datetime) -> datetime:
        now = self._clock.now()
        return max((now, *earliest))

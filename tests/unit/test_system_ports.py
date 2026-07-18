"""Default system adapter tests."""

from agent_factory.application.ports import Clock, CorrelationContext, IdGenerator
from agent_factory.infrastructure.system import (
    ContextVarCorrelationContext,
    SystemClock,
    UUID4Generator,
)


def test_default_adapters_satisfy_application_protocols() -> None:
    clock: Clock = SystemClock()
    id_generator: IdGenerator = UUID4Generator()
    correlation_context: CorrelationContext = ContextVarCorrelationContext()

    assert clock.now().utcoffset() is not None
    assert id_generator.new() != id_generator.new()
    assert correlation_context.get() is None


def test_correlation_context_restores_previous_value() -> None:
    context = ContextVarCorrelationContext()

    outer_token = context.set("request-1")
    inner_token = context.set("request-2")
    assert context.get() == "request-2"

    context.reset(inner_token)
    assert context.get() == "request-1"

    context.reset(outer_token)
    assert context.get() is None


def test_correlation_context_rejects_blank_ids() -> None:
    context = ContextVarCorrelationContext()

    try:
        context.set("  ")
    except ValueError as exc:
        assert str(exc) == "correlation_id must not be blank"
    else:
        raise AssertionError("blank correlation ID was accepted")

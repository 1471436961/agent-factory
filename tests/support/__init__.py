"""Shared test-only infrastructure helpers."""

from tests.support.fault_injection import (
    EntityWriteTarget,
    FaultInjectingUnitOfWorkFactory,
    FaultPoint,
    InjectedTransactionFailure,
)

__all__ = [
    "EntityWriteTarget",
    "FaultInjectingUnitOfWorkFactory",
    "FaultPoint",
    "InjectedTransactionFailure",
]

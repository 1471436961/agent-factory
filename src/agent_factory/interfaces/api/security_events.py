"""Allowlisted security event logging for the HTTP trust boundary."""

from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger("agent_factory.security")


def log_authentication_rejected(
    *,
    correlation_id: UUID,
    category: str,
    credential_present: bool,
) -> None:
    """Record an authentication rejection without request or credential data."""

    logger.warning(
        "authentication_rejected",
        extra={
            "correlation_id": str(correlation_id),
            "security_category": category,
            "credential_present": credential_present,
        },
    )


def log_authorization_rejected(
    *,
    correlation_id: UUID,
    error_code: str,
) -> None:
    """Record an authorization rejection using only stable identifiers."""

    logger.warning(
        "authorization_rejected",
        extra={
            "correlation_id": str(correlation_id),
            "security_category": error_code,
            "credential_present": True,
        },
    )

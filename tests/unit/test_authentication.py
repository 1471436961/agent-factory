"""Fail-closed authentication adapter tests."""

from pydantic import SecretStr

from agent_factory.application.security import FactoryRole, Principal
from agent_factory.infrastructure.authentication import (
    StaticBearerAuthenticator,
    UnavailableAuthenticator,
)

TOKEN = "test-token-that-is-at-least-32-characters"


def test_static_authenticator_returns_principal_only_for_exact_token() -> None:
    principal = Principal(
        subject="local-owner",
        roles=frozenset({FactoryRole.ADMIN}),
    )
    authenticator = StaticBearerAuthenticator.from_secret(
        SecretStr(TOKEN),
        principal,
    )

    assert authenticator.ready is True
    assert authenticator.authenticate(TOKEN) == principal
    assert authenticator.authenticate(f"{TOKEN}-wrong") is None
    assert authenticator.authenticate("") is None


def test_static_authenticator_repr_does_not_expose_token() -> None:
    authenticator = StaticBearerAuthenticator.from_secret(
        SecretStr(TOKEN),
        Principal(
            subject="local-owner",
            roles=frozenset({FactoryRole.ADMIN}),
        ),
    )

    assert TOKEN not in repr(authenticator)
    assert "digest" not in repr(authenticator)


def test_unavailable_authenticator_fails_closed() -> None:
    authenticator = UnavailableAuthenticator()

    assert authenticator.ready is False
    assert authenticator.authenticate(TOKEN) is None

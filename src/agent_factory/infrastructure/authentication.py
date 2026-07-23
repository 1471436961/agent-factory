"""Fail-closed authentication adapters for the Alpha interface boundary."""

import hashlib
import hmac
from dataclasses import dataclass, field

from pydantic import SecretStr

from agent_factory.application.security import Principal


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class StaticBearerAuthenticator:
    """Authenticate one configured principal without retaining its raw token."""

    _digest: bytes = field(repr=False)
    _principal: Principal

    @classmethod
    def from_secret(
        cls,
        token: SecretStr,
        principal: Principal,
    ) -> "StaticBearerAuthenticator":
        return cls(
            _digest=_token_digest(token.get_secret_value()),
            _principal=principal,
        )

    @property
    def ready(self) -> bool:
        return True

    def authenticate(self, bearer_token: str) -> Principal | None:
        if hmac.compare_digest(self._digest, _token_digest(bearer_token)):
            return self._principal
        return None


@dataclass(frozen=True, slots=True)
class UnavailableAuthenticator:
    """Reject authentication while required server configuration is absent."""

    @property
    def ready(self) -> bool:
        return False

    def authenticate(self, bearer_token: str) -> Principal | None:
        return None

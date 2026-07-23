"""Transport-neutral identity and authorization contracts."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Protocol

from pydantic import Field

from agent_factory.domain.common import Actor, FrozenModel
from agent_factory.domain.errors import AuthorizationDeniedError


class FactoryRole(StrEnum):
    """Stable roles assigned by an authentication adapter."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    AUDITOR = "auditor"
    ADMIN = "admin"


class FactoryPermission(StrEnum):
    """Application permissions shared by REST and future tool adapters."""

    FACTORY_READ = "factory:read"
    FACTORY_WRITE = "factory:write"
    AUDIT_READ = "audit:read"


class Principal(FrozenModel):
    """Authenticated subject passed across interface adapters."""

    subject: Actor
    roles: Annotated[frozenset[FactoryRole], Field(min_length=1)]


class Authenticator(Protocol):
    """Authenticate one opaque bearer credential without transport coupling."""

    @property
    def ready(self) -> bool:
        """Return whether this adapter can authenticate requests."""

    def authenticate(self, bearer_token: str) -> Principal | None:
        """Return the configured principal for a valid token."""


ROLE_PERMISSIONS: Mapping[FactoryRole, frozenset[FactoryPermission]] = MappingProxyType(
    {
        FactoryRole.VIEWER: frozenset({FactoryPermission.FACTORY_READ}),
        FactoryRole.OPERATOR: frozenset(
            {
                FactoryPermission.FACTORY_READ,
                FactoryPermission.FACTORY_WRITE,
            }
        ),
        FactoryRole.AUDITOR: frozenset(
            {
                FactoryPermission.FACTORY_READ,
                FactoryPermission.AUDIT_READ,
            }
        ),
        FactoryRole.ADMIN: frozenset(
            {
                FactoryPermission.FACTORY_READ,
                FactoryPermission.FACTORY_WRITE,
                FactoryPermission.AUDIT_READ,
            }
        ),
    }
)


class AuthorizationPolicy:
    """Apply the fixed Alpha role-to-permission matrix."""

    @staticmethod
    def allows(principal: Principal, permission: FactoryPermission) -> bool:
        granted = frozenset(
            item for role in principal.roles for item in ROLE_PERMISSIONS[role]
        )
        return permission in granted

    def require(
        self,
        principal: Principal,
        permission: FactoryPermission,
    ) -> None:
        if not self.allows(principal, permission):
            raise AuthorizationDeniedError(
                details={"required_permission": permission.value}
            )

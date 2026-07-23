"""Identity and authorization policy tests."""

import pytest
from pydantic import ValidationError

from agent_factory.application.security import (
    AuthorizationPolicy,
    FactoryPermission,
    FactoryRole,
    Principal,
)
from agent_factory.domain.errors import AuthorizationDeniedError


@pytest.mark.parametrize(
    ("role", "permission", "expected"),
    [
        (FactoryRole.VIEWER, FactoryPermission.FACTORY_READ, True),
        (FactoryRole.VIEWER, FactoryPermission.FACTORY_WRITE, False),
        (FactoryRole.VIEWER, FactoryPermission.AUDIT_READ, False),
        (FactoryRole.OPERATOR, FactoryPermission.FACTORY_READ, True),
        (FactoryRole.OPERATOR, FactoryPermission.FACTORY_WRITE, True),
        (FactoryRole.OPERATOR, FactoryPermission.AUDIT_READ, False),
        (FactoryRole.AUDITOR, FactoryPermission.FACTORY_READ, True),
        (FactoryRole.AUDITOR, FactoryPermission.FACTORY_WRITE, False),
        (FactoryRole.AUDITOR, FactoryPermission.AUDIT_READ, True),
        (FactoryRole.ADMIN, FactoryPermission.FACTORY_READ, True),
        (FactoryRole.ADMIN, FactoryPermission.FACTORY_WRITE, True),
        (FactoryRole.ADMIN, FactoryPermission.AUDIT_READ, True),
    ],
)
def test_authorization_policy_applies_role_matrix(
    role: FactoryRole,
    permission: FactoryPermission,
    expected: bool,
) -> None:
    principal = Principal(subject="owner", roles=frozenset({role}))

    assert AuthorizationPolicy.allows(principal, permission) is expected


def test_authorization_policy_combines_multiple_roles() -> None:
    principal = Principal(
        subject="owner",
        roles=frozenset({FactoryRole.OPERATOR, FactoryRole.AUDITOR}),
    )
    policy = AuthorizationPolicy()

    policy.require(principal, FactoryPermission.FACTORY_WRITE)
    policy.require(principal, FactoryPermission.AUDIT_READ)


def test_authorization_policy_raises_stable_denial() -> None:
    principal = Principal(
        subject="viewer",
        roles=frozenset({FactoryRole.VIEWER}),
    )

    with pytest.raises(AuthorizationDeniedError) as captured:
        AuthorizationPolicy().require(
            principal,
            FactoryPermission.FACTORY_WRITE,
        )

    assert captured.value.code == "AUTHORIZATION_DENIED"
    assert captured.value.details == {
        "required_permission": FactoryPermission.FACTORY_WRITE.value
    }


def test_principal_requires_at_least_one_role() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        Principal(subject="owner", roles=frozenset())

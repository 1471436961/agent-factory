"""Immutable HTTP operation manifest for the public Python SDK."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

HttpMethod = Literal["GET", "POST"]


@dataclass(frozen=True, slots=True)
class SdkOperation:
    method: HttpMethod
    path: str
    authenticated: bool
    api_scoped: bool


SDK_OPERATIONS: MappingProxyType[str, SdkOperation] = MappingProxyType(
    {
        "check_liveness": SdkOperation("GET", "/health/live", False, False),
        "check_readiness": SdkOperation("GET", "/health/ready", False, False),
        "register_prototype": SdkOperation("POST", "/prototypes", True, True),
        "list_prototypes": SdkOperation("GET", "/prototypes", True, True),
        "publish_prototype": SdkOperation(
            "POST",
            "/prototypes/{prototype_id}/versions/{version}/publish",
            True,
            True,
        ),
        "deprecate_prototype": SdkOperation(
            "POST",
            "/prototypes/{prototype_id}/versions/{version}/deprecate",
            True,
            True,
        ),
        "clone_agent": SdkOperation(
            "POST",
            "/prototypes/{prototype_id}/versions/{version}/instances",
            True,
            True,
        ),
        "register_knowledge": SdkOperation("POST", "/knowledge", True, True),
        "bind_knowledge": SdkOperation(
            "POST",
            "/instances/{instance_id}/knowledge-bindings",
            True,
            True,
        ),
        "export_spec": SdkOperation(
            "POST",
            "/instances/{instance_id}/spec-exports",
            True,
            True,
        ),
        "transition_instance": SdkOperation(
            "POST",
            "/instances/{instance_id}/transitions",
            True,
            True,
        ),
        "register_evaluation_suite": SdkOperation(
            "POST",
            "/evaluation-suites",
            True,
            True,
        ),
        "get_evaluation_suite": SdkOperation(
            "GET",
            "/evaluation-suites/{suite_id}/versions/{version}",
            True,
            True,
        ),
        "register_skill_tree": SdkOperation("POST", "/skill-trees", True, True),
        "get_skill_tree": SdkOperation(
            "GET",
            "/skill-trees/{tree_id}/versions/{version}",
            True,
            True,
        ),
        "evaluate_instance": SdkOperation(
            "POST",
            "/instances/{instance_id}/evaluations",
            True,
            True,
        ),
        "review_evaluation": SdkOperation(
            "POST",
            "/evaluation-reports/{report_id}/reviews",
            True,
            True,
        ),
        "promote_agent": SdkOperation(
            "POST",
            "/instances/{instance_id}/promotions",
            True,
            True,
        ),
        "record_task_outcome": SdkOperation(
            "POST",
            "/instances/{instance_id}/task-outcomes",
            True,
            True,
        ),
        "query_audit": SdkOperation("GET", "/audit-events", True, True),
    }
)

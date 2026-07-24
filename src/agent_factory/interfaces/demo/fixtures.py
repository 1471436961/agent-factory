"""Fixed, validated SDK requests for the deterministic Writer demonstration."""

from __future__ import annotations

import hashlib
from uuid import UUID

from agent_factory.sdk import (
    BindKnowledgeRequest,
    CloneAgentRequest,
    EvaluateInstanceRequest,
    ExportSpecRequest,
    PromoteAgentRequest,
    RegisterEvaluationSuiteRequest,
    RegisterKnowledgeRequest,
    RegisterPrototypeRequest,
    RegisterSkillTreeRequest,
    ReviewEvaluationRequest,
    TransitionInstanceRequest,
)

PROTOTYPE_ID = "technical-writer"
PROTOTYPE_VERSION = "1.0.0"
KNOWLEDGE_ID = "agent-factory-docs"
KNOWLEDGE_VERSION = "1.0.0"
KNOWLEDGE_SLOT = "product-docs"
TREE_ID = "writer-skills"
TREE_VERSION = "1.0.0"
SUITE_ID = "mid-writer-suite"
SUITE_VERSION = "1.0.0"
TARGET_NODE_ID = "mid-writer"
RUNTIME_NAME = "demo-runtime"
TOOL_NAME = "document-search"
CASE_ID = "agent-factory-overview"
TASK_INPUT = "Write a concise Agent Factory AgentSpec knowledge and audit overview."
KNOWLEDGE_CONTENT = (
    "Agent Factory creates immutable AgentSpec snapshots.\n"
    "Each AgentSpec records prototype, knowledge, tool and skill-tree checksums for "
    "audit.\n"
    "Knowledge slots keep versioned product documentation separate from Agent "
    "logic.\n"
    "Skill promotion requires evaluation evidence, explicit review and a new revision."
)
KNOWLEDGE_CHECKSUM = hashlib.sha256(KNOWLEDGE_CONTENT.encode("utf-8")).hexdigest()

OUTPUT_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "body": {"type": "string", "minLength": 1},
    },
    "required": ["title", "body"],
    "additionalProperties": False,
}


def evaluation_suite_request() -> RegisterEvaluationSuiteRequest:
    return RegisterEvaluationSuiteRequest.model_validate(
        {
            "suite_id": SUITE_ID,
            "version": SUITE_VERSION,
            "rules": [
                {
                    "rule_id": "contains-governance-evidence",
                    "kind": "required-terms",
                    "parameters": {
                        "terms": ["AgentSpec", "knowledge", "audit"],
                    },
                },
                {
                    "rule_id": "matches-writer-schema",
                    "kind": "json-schema",
                    "parameters": {"schema": OUTPUT_SCHEMA},
                },
                {
                    "rule_id": "uses-authorized-search",
                    "kind": "tool-called",
                    "parameters": {"tool_name": TOOL_NAME},
                },
            ],
            "cases": [{"case_id": CASE_ID, "input": TASK_INPUT}],
            "minimum_soft_score": 1.0,
            "require_manual_review": True,
        }
    )


def skill_tree_request(suite_checksum: str) -> RegisterSkillTreeRequest:
    return RegisterSkillTreeRequest.model_validate(
        {
            "tree_id": TREE_ID,
            "version": TREE_VERSION,
            "nodes": [
                {
                    "node_id": TARGET_NODE_ID,
                    "display_name": "Mid Writer",
                    "parents": [],
                    "prompt_appendix": (
                        "Cite the bound product documentation and preserve provenance."
                    ),
                    "granted_tools": [TOOL_NAME],
                    "added_knowledge_slots": [],
                    "evaluation_suite": {
                        "suite_id": SUITE_ID,
                        "version": SUITE_VERSION,
                        "checksum": suite_checksum,
                    },
                    "observation_policy": {
                        "window_size": 5,
                        "minimum_samples": 3,
                        "consecutive_failures": 2,
                        "failure_rate_threshold": 0.6,
                    },
                }
            ],
        }
    )


def prototype_request(tree_checksum: str) -> RegisterPrototypeRequest:
    return RegisterPrototypeRequest.model_validate(
        {
            "prototype_id": PROTOTYPE_ID,
            "version": PROTOTYPE_VERSION,
            "definition": {
                "agent_type": "technical-writer",
                "role": "Technical Writer",
                "system_prompt": (
                    "Write concise technical documentation from verified knowledge."
                ),
                "tools": [TOOL_NAME],
                "capabilities": ["can-write"],
                "output_schema": OUTPUT_SCHEMA,
                "knowledge_slots": [
                    {
                        "name": KNOWLEDGE_SLOT,
                        "required": True,
                        "accepted_kinds": ["document"],
                        "min_version": KNOWLEDGE_VERSION,
                        "max_version_exclusive": "2.0.0",
                        "injection_mode": "inline",
                        "multiple": False,
                        "max_items": 1,
                    }
                ],
                "metadata": {"demo": "m3.6", "scenario": "fixed-writer"},
            },
            "skill_tree": {
                "tree_id": TREE_ID,
                "version": TREE_VERSION,
                "checksum": tree_checksum,
            },
            "publish": False,
        }
    )


def knowledge_request() -> RegisterKnowledgeRequest:
    return RegisterKnowledgeRequest.model_validate(
        {
            "knowledge_id": KNOWLEDGE_ID,
            "version": KNOWLEDGE_VERSION,
            "name": "Agent Factory product documentation",
            "kind": "document",
            "content": KNOWLEDGE_CONTENT,
            "mime_type": "text/plain",
            "checksum": KNOWLEDGE_CHECKSUM,
            "tags": ["agent-factory", "demo"],
        }
    )


def clone_request() -> CloneAgentRequest:
    return CloneAgentRequest(runtime_target=RUNTIME_NAME)


def bind_request(expected_revision: int) -> BindKnowledgeRequest:
    return BindKnowledgeRequest.model_validate(
        {
            "expected_revision": expected_revision,
            "selections": [
                {
                    "slot_name": KNOWLEDGE_SLOT,
                    "knowledge_id": KNOWLEDGE_ID,
                    "version": KNOWLEDGE_VERSION,
                }
            ],
        }
    )


def export_request(revision: int) -> ExportSpecRequest:
    return ExportSpecRequest(revision=revision)


def transition_request(
    expected_revision: int,
    target_status: str,
    reason: str,
) -> TransitionInstanceRequest:
    return TransitionInstanceRequest.model_validate(
        {
            "expected_revision": expected_revision,
            "target_status": target_status,
            "reason": reason,
        }
    )


def evaluation_request(
    *,
    expected_revision: int,
    suite_checksum: str,
    output_text: str,
    structured_output: object,
    tool_was_called: bool,
) -> EvaluateInstanceRequest:
    return EvaluateInstanceRequest.model_validate(
        {
            "expected_revision": expected_revision,
            "suite": {
                "suite_id": SUITE_ID,
                "version": SUITE_VERSION,
                "checksum": suite_checksum,
            },
            "runtime_model": RUNTIME_NAME,
            "case_results": [
                {
                    "case_id": CASE_ID,
                    "output_text": output_text,
                    "structured_output": structured_output,
                    "called_tools": [TOOL_NAME] if tool_was_called else [],
                }
            ],
        }
    )


def review_request() -> ReviewEvaluationRequest:
    return ReviewEvaluationRequest.model_validate(
        {
            "decision": "approved",
            "comment": "Approved by the human operator in the M3.6 demo.",
        }
    )


def promotion_request(
    *,
    expected_revision: int,
    report_id: UUID,
    review_id: UUID,
) -> PromoteAgentRequest:
    return PromoteAgentRequest(
        expected_revision=expected_revision,
        target_node_id=TARGET_NODE_ID,
        evaluation_report_id=report_id,
        evaluation_review_id=review_id,
    )

"""Pure contract tests for fixed M3.6 fixtures and UI-safe state."""

import hashlib

import pytest
from pydantic import ValidationError

from agent_factory.interfaces.demo.contracts import (
    DemoPhase,
    DemoSession,
    DemoSourceView,
)
from agent_factory.interfaces.demo.fixtures import (
    KNOWLEDGE_CHECKSUM,
    KNOWLEDGE_CONTENT,
    PROTOTYPE_ID,
    SUITE_ID,
    TARGET_NODE_ID,
    TREE_ID,
    evaluation_suite_request,
    knowledge_request,
    prototype_request,
    skill_tree_request,
)


def test_fixed_requests_are_validated_and_reference_supplied_checksums() -> None:
    suite = evaluation_suite_request()
    tree = skill_tree_request("a" * 64)
    prototype = prototype_request("b" * 64)
    knowledge = knowledge_request()

    assert suite.suite_id == SUITE_ID
    assert suite.require_manual_review is True
    assert {rule.kind.value for rule in suite.rules} == {
        "json-schema",
        "required-terms",
        "tool-called",
    }
    assert tree.tree_id == TREE_ID
    assert tree.nodes[0].node_id == TARGET_NODE_ID
    assert tree.nodes[0].evaluation_suite.checksum == "a" * 64
    assert prototype.prototype_id == PROTOTYPE_ID
    assert prototype.skill_tree is not None
    assert prototype.skill_tree.checksum == "b" * 64
    assert (
        knowledge.checksum
        == hashlib.sha256(KNOWLEDGE_CONTENT.encode("utf-8")).hexdigest()
    )
    assert knowledge.checksum == KNOWLEDGE_CHECKSUM


def test_session_checkpoint_is_immutable_and_sources_replace_by_type() -> None:
    original = DemoSession()
    first_source = DemoSourceView(
        source_type="prototype",
        source_id="technical-writer",
        version="1.0.0",
        checksum="a" * 64,
    )
    replacement = first_source.model_copy(update={"checksum": "b" * 64})

    checkpoint = original.replace_source(first_source).checkpoint("registered")
    replaced = checkpoint.replace_source(replacement)

    assert original.completed_operations == frozenset()
    assert checkpoint.is_completed("registered") is True
    assert checkpoint.source("prototype") == first_source
    assert replaced.source("prototype") == replacement
    assert len(replaced.sources) == 1


def test_advanced_phase_requires_runtime_and_review_evidence() -> None:
    with pytest.raises(ValidationError):
        DemoSession(phase=DemoPhase.AWAITING_REVIEW)

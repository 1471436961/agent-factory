"""Tests for the metadata-only infrastructure tool catalog."""

import pytest

from agent_factory.domain.enums import ToolPermission
from agent_factory.infrastructure.tool_catalog import (
    InMemoryToolCatalog,
    default_tool_catalog,
)


def test_default_catalog_contains_read_only_document_search_metadata() -> None:
    catalog = default_tool_catalog()

    tool = catalog.get("document-search")
    assert catalog.names() == frozenset({"document-search"})
    assert tool is not None
    assert tool.permission_tags == frozenset({ToolPermission.READ_ONLY})
    assert tool.input_schema["type"] == "object"


def test_catalog_rejects_duplicate_names() -> None:
    tool = default_tool_catalog().get("document-search")
    assert tool is not None

    with pytest.raises(ValueError, match="duplicate"):
        InMemoryToolCatalog((tool, tool))

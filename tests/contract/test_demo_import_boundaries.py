"""Static dependency guard for the optional Gradio presentation package."""

import ast
from pathlib import Path


def test_demo_package_does_not_import_core_implementation_layers() -> None:
    package = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agent_factory"
        / "interfaces"
        / "demo"
    )
    forbidden = (
        "agent_factory.container",
        "agent_factory.domain",
        "agent_factory.infrastructure",
        "agent_factory.interfaces.api",
        "agent_factory.application.controller",
        "agent_factory.application.repositories",
        "agent_factory.application.unit_of_work",
    )
    imported: list[tuple[Path, str]] = []

    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.append((source_path, node.module))
            elif isinstance(node, ast.Import):
                imported.extend((source_path, name.name) for name in node.names)

    violations = [
        f"{path.name}: {module}"
        for path, module in imported
        if module.startswith(forbidden)
    ]
    assert violations == []


def test_demo_application_dependency_is_runtime_contract_only() -> None:
    package = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agent_factory"
        / "interfaces"
        / "demo"
    )
    application_imports: set[str] = set()

    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("agent_factory.application")
            ):
                application_imports.add(node.module)

    assert application_imports == {"agent_factory.application.runtime"}

"""Unit evidence for the local Alpha distribution smoke helpers."""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import scripts.local_alpha_smoke as smoke_module
from scripts.local_alpha_smoke import (
    EXPECTED_MIGRATIONS,
    EXPECTED_WHEEL_RESOURCES,
    ServerProcess,
    SmokeFailure,
    _graceful_shutdown_completed,
    _migration_versions,
    _port_bind_failed,
    _remove_run_directory,
    _required_platform_int,
    _safe_archive_path,
    _server_environment,
    _start_ready_server,
    assert_sensitive_values_absent,
    discover_distributions,
    isolated_environment,
    source_package_resources,
    verify_distributions,
)


def test_required_platform_int_resolves_only_integer_constants() -> None:
    namespace = SimpleNamespace(present=512, wrong_type="512")

    assert _required_platform_int(namespace, "present") == 512
    with pytest.raises(SmokeFailure, match="unavailable: missing"):
        _required_platform_int(namespace, "missing")
    with pytest.raises(SmokeFailure, match="unavailable: wrong_type"):
        _required_platform_int(namespace, "wrong_type")


def _write_distribution_pair(
    root: Path,
    *,
    omitted_wheel_resource: str | None = None,
    entry_point: str = "agent_factory.demo:main",
) -> tuple[Path, Path]:
    dist = root / "dist"
    dist.mkdir()
    wheel = dist / "agent_factory-1.0.0a1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        for name in EXPECTED_WHEEL_RESOURCES:
            if name != omitted_wheel_resource:
                archive.writestr(name, "# fixture\n")
        archive.writestr(
            "agent_factory-1.0.0a1.dist-info/METADATA",
            "Metadata-Version: 2.4\n"
            "Name: agent-factory\n"
            "Version: 1.0.0a1\n"
            "Provides-Extra: demo\n"
            "Provides-Extra: llm\n",
        )
        archive.writestr(
            "agent_factory-1.0.0a1.dist-info/entry_points.txt",
            f"[console_scripts]\nagent-factory-demo = {entry_point}\n",
        )

    source = root / "source" / "agent_factory-1.0.0a1"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (source / "README.md").write_text("# Agent Factory\n", encoding="utf-8")
    for name in EXPECTED_MIGRATIONS:
        target = source / "src" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SELECT 1;\n", encoding="utf-8")
    sdist = dist / "agent_factory-1.0.0a1.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        archive.add(source, arcname=source.name)
    return wheel, sdist


def test_distribution_pair_has_reviewed_resources_metadata_and_entry_point(
    tmp_path: Path,
) -> None:
    wheel, sdist = _write_distribution_pair(tmp_path)

    discovered = discover_distributions(wheel.parent)
    artifacts = verify_distributions(*discovered)

    assert discovered == (wheel.resolve(), sdist.resolve())
    assert artifacts.version == "1.0.0a1"
    assert artifacts.wheel == wheel.resolve()
    assert artifacts.sdist == sdist.resolve()


def test_distribution_verification_rejects_missing_resource_and_bad_entry_point(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    wheel, sdist = _write_distribution_pair(
        missing_root,
        omitted_wheel_resource=EXPECTED_MIGRATIONS[-1],
    )
    with pytest.raises(SmokeFailure, match="missing required resources"):
        verify_distributions(wheel, sdist)

    entry_root = tmp_path / "entry"
    entry_root.mkdir()
    wheel, sdist = _write_distribution_pair(
        entry_root,
        entry_point="unexpected.module:main",
    )
    with pytest.raises(SmokeFailure, match="invalid agent-factory-demo"):
        verify_distributions(wheel, sdist)


def test_source_resources_are_derived_and_missing_module_fails_distribution(
    tmp_path: Path,
) -> None:
    package = tmp_path / "src" / "agent_factory"
    (package / "nested").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "nested" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "ignored.txt").write_text("not Python\n", encoding="utf-8")
    resources = source_package_resources(package)
    assert resources == frozenset(
        {
            "agent_factory/__init__.py",
            "agent_factory/nested/module.py",
        }
    )

    wheel, sdist = _write_distribution_pair(tmp_path)
    with pytest.raises(SmokeFailure, match=r"nested/module\.py"):
        verify_distributions(
            wheel,
            sdist,
            expected_source_resources=resources,
        )


def test_distribution_discovery_requires_one_fresh_artifact_pair(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "one.whl").touch()
    (dist / "two.whl").touch()
    (dist / "one.tar.gz").touch()

    with pytest.raises(SmokeFailure, match="exactly one wheel"):
        discover_distributions(dist)


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("agent_factory/module.py", True),
        ("root/../secret.txt", False),
        ("../secret.txt", False),
        ("/absolute/path", False),
        ("", False),
    ),
)
def test_archive_member_paths_are_relative_and_cannot_traverse(
    name: str,
    expected: bool,
) -> None:
    assert _safe_archive_path(name) is expected


def test_isolated_environment_removes_source_and_external_factory_settings(
    tmp_path: Path,
) -> None:
    source = {
        "PATH": "retained",
        "PYTHONPATH": "workspace/src",
        "PYTHONHOME": "unexpected",
        "VIRTUAL_ENV": "workspace/.venv",
        "AGENT_FACTORY_AUTH_TOKEN": "external-secret",
        "AGENT_FACTORY_DATABASE_URL": "external.db",
    }

    environment = isolated_environment(
        source,
        temp_dir=tmp_path / "temp",
        uv_cache_dir=tmp_path / "uv-cache",
    )

    assert environment["PATH"] == "retained"
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert not any(key.startswith("AGENT_FACTORY_") for key in environment)
    assert environment["TEMP"] == str(tmp_path / "temp")
    assert environment["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")


def test_server_environment_uses_one_explicit_local_identity(tmp_path: Path) -> None:
    environment = _server_environment(
        {"PATH": "retained"},
        token="smoke-token-at-least-32-characters",
        database_path=tmp_path / "factory.db",
        data_dir=tmp_path,
    )

    assert environment["PATH"] == "retained"
    assert environment["AGENT_FACTORY_AUTH_SUBJECT"] == "local-smoke-owner"
    assert environment["AGENT_FACTORY_AUTH_ROLES"] == '["admin"]'
    assert environment["AGENT_FACTORY_DATABASE_URL"].endswith("/factory.db")


def test_sensitive_value_scan_reports_location_without_echoing_secret() -> None:
    token = "unique-smoke-secret"
    with pytest.raises(SmokeFailure) as captured:
        assert_sensitive_values_absent(
            f"log accidentally contains {token}",
            (token,),
            location="server log",
        )

    assert "server log" in str(captured.value)
    assert token not in str(captured.value)


@pytest.mark.parametrize(
    ("return_code", "platform_name", "log", "expected"),
    (
        (0, "posix", "", True),
        (0, "nt", "", True),
        (
            3,
            "nt",
            "Application shutdown complete.\nFinished server process [1]",
            True,
        ),
        (3, "nt", "Application shutdown complete.", False),
        (3, "posix", "Application shutdown complete.\nFinished server process", False),
        (1, "nt", "Application shutdown complete.\nFinished server process", False),
        (
            -int(signal.SIGTERM),
            "posix",
            "Application shutdown complete.\nFinished server process [1]",
            True,
        ),
        (-int(signal.SIGTERM), "posix", "Application shutdown complete.", False),
        (
            -int(signal.SIGTERM),
            "nt",
            "Application shutdown complete.\nFinished server process",
            False,
        ),
        (
            -int(signal.SIGINT),
            "posix",
            "Application shutdown complete.\nFinished server process",
            False,
        ),
    ),
)
def test_graceful_shutdown_requires_platform_specific_exit_evidence(
    return_code: int,
    platform_name: str,
    log: str,
    expected: bool,
) -> None:
    assert (
        _graceful_shutdown_completed(
            return_code,
            log,
            platform_name=platform_name,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("log", "expected"),
    (
        ("ERROR: [Errno 98] address already in use", True),
        ("OSError: [WinError 10048] Only one usage of each socket address", True),
        ("Application startup failed due to migration", False),
    ),
)
def test_port_retry_only_handles_explicit_bind_conflicts(
    log: str,
    expected: bool,
) -> None:
    assert _port_bind_failed(log) is expected


def test_readiness_interrupt_force_stops_the_unpublished_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ServerProcess(
        process=cast(subprocess.Popen[str], object()),
        port=12345,
        log_path=tmp_path / "server.log",
    )
    stopped: list[ServerProcess] = []
    monkeypatch.setattr(smoke_module, "_start_server", lambda **_: server)

    def interrupt(_: ServerProcess, __: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(smoke_module, "_wait_for_readiness", interrupt)
    monkeypatch.setattr(
        smoke_module,
        "_force_stop",
        lambda selected, _: stopped.append(selected),
    )

    with pytest.raises(KeyboardInterrupt):
        _start_ready_server(
            python=tmp_path / "python",
            cwd=tmp_path,
            environment={},
            log_prefix=tmp_path / "server",
            startup_timeout=1.0,
            shutdown_timeout=1.0,
        )

    assert stopped == [server]


def test_migration_history_is_read_from_the_stopped_runtime_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "factory.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations (version) VALUES (?)",
            ((2,), (1,), (3,)),
        )
        connection.commit()

    assert _migration_versions(database) == (1, 2, 3)


def test_cleanup_only_removes_one_generated_run_directory(tmp_path: Path) -> None:
    work_root = tmp_path / "work"
    run_dir = work_root / "run-fixture"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.txt").write_text("complete", encoding="utf-8")

    _remove_run_directory(run_dir, work_root)

    assert not run_dir.exists()
    assert work_root.exists()


@pytest.mark.parametrize(
    "unsafe",
    (
        Path("outside"),
        Path("work/not-a-run"),
        Path("work/nested/run-fixture"),
    ),
)
def test_cleanup_rejects_paths_outside_one_generated_run(
    tmp_path: Path,
    unsafe: Path,
) -> None:
    work_root = tmp_path / "work"
    candidate = tmp_path / unsafe
    candidate.mkdir(parents=True)

    with pytest.raises(SmokeFailure, match="refusing to clean"):
        _remove_run_directory(candidate, work_root)

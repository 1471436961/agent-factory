"""Build and exercise Agent Factory from isolated wheel installations.

This command deliberately imports no ``agent_factory`` modules. It orchestrates
fresh environments with the standard library, then asks the isolated Python
interpreter to import the installed wheel. The smoke covers package resources,
minimal and optional dependencies, a real loopback Uvicorn process, SDK access,
and SQLite recovery after process restart.
"""

from __future__ import annotations

import argparse
import configparser
import http.client
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGE_NAME: Final = "agent-factory"
EXPECTED_MIGRATIONS: Final = tuple(
    f"agent_factory/infrastructure/sqlite/sql/{name}"
    for name in (
        "001_initial.sql",
        "002_persistence_contracts.sql",
        "003_skill_governance.sql",
        "004_instance_configuration_checksum.sql",
        "005_task_outcome_integrity.sql",
        "006_tool_call_records.sql",
    )
)
EXPECTED_WHEEL_RESOURCES: Final = frozenset(
    {
        "agent_factory/settings.py",
        "agent_factory/sdk/client.py",
        "agent_factory/demo.py",
        "agent_factory/interfaces/api/main.py",
        *EXPECTED_MIGRATIONS,
    }
)
PROTOTYPE_ID: Final = "local-alpha-smoke"
PROTOTYPE_VERSION: Final = "1.0.0"
AGENT_TYPE: Final = "local-alpha-smoke-agent"


class SmokeFailure(RuntimeError):
    """A stable, secret-free failure from the release smoke."""


@dataclass(frozen=True, slots=True)
class DistributionArtifacts:
    wheel: Path
    sdist: Path
    version: str


@dataclass(frozen=True, slots=True)
class ServerProcess:
    process: subprocess.Popen[str]
    port: int
    log_path: Path


@dataclass(frozen=True, slots=True)
class SmokeSummary:
    version: str
    wheel_name: str
    migration_versions: tuple[int, ...]
    prototype_id: str


def _safe_archive_path(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _one_path(paths: Sequence[Path], description: str) -> Path:
    if len(paths) != 1:
        raise SmokeFailure(f"expected exactly one {description}, found {len(paths)}")
    return paths[0]


def discover_distributions(dist_dir: Path) -> tuple[Path, Path]:
    """Return the single wheel and sdist from a fresh build directory."""

    wheel = _one_path(sorted(dist_dir.glob("*.whl")), "wheel")
    sdist = _one_path(sorted(dist_dir.glob("*.tar.gz")), "sdist")
    return wheel.resolve(), sdist.resolve()


def _wheel_metadata(archive: zipfile.ZipFile) -> Message:
    names = [
        name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
    ]
    metadata_name = _one_path([Path(name) for name in names], "wheel METADATA")
    return BytesParser(policy=policy.default).parsebytes(
        archive.read(metadata_name.as_posix())
    )


def _wheel_entry_points(archive: zipfile.ZipFile) -> configparser.ConfigParser:
    names = [
        name
        for name in archive.namelist()
        if name.endswith(".dist-info/entry_points.txt")
    ]
    entry_points_name = _one_path(
        [Path(name) for name in names],
        "wheel entry_points.txt",
    )
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(archive.read(entry_points_name.as_posix()).decode("utf-8"))
    return parser


def verify_distributions(wheel: Path, sdist: Path) -> DistributionArtifacts:
    """Validate release metadata and package data without extracting archives."""

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        unsafe = [name for name in wheel_names if not _safe_archive_path(name)]
        if unsafe:
            raise SmokeFailure("wheel contains an unsafe archive path")
        missing = EXPECTED_WHEEL_RESOURCES.difference(wheel_names)
        if missing:
            raise SmokeFailure(
                "wheel is missing required resources: " + ", ".join(sorted(missing))
            )
        metadata = _wheel_metadata(archive)
        entry_points = _wheel_entry_points(archive)

    if metadata.get("Name") != PACKAGE_NAME:
        raise SmokeFailure("wheel project name does not match agent-factory")
    version = metadata.get("Version")
    if not version:
        raise SmokeFailure("wheel metadata has no version")
    extras = frozenset(metadata.get_all("Provides-Extra") or ())
    if not {"demo", "llm"}.issubset(extras):
        raise SmokeFailure("wheel metadata is missing demo or llm extra")
    if (
        not entry_points.has_section("console_scripts")
        or entry_points.get(
            "console_scripts",
            "agent-factory-demo",
            fallback=None,
        )
        != "agent_factory.demo:main"
    ):
        raise SmokeFailure("wheel has an invalid agent-factory-demo entry point")

    with tarfile.open(sdist, mode="r:gz") as archive:
        sdist_names = [member.name for member in archive.getmembers()]
    if any(not _safe_archive_path(name) for name in sdist_names):
        raise SmokeFailure("sdist contains an unsafe archive path")
    expected_sdist_suffixes = {
        "pyproject.toml",
        "README.md",
        *EXPECTED_MIGRATIONS,
    }
    for suffix in expected_sdist_suffixes:
        if not any(name.endswith(suffix) for name in sdist_names):
            raise SmokeFailure(f"sdist is missing required resource: {suffix}")

    return DistributionArtifacts(wheel=wheel, sdist=sdist, version=version)


def isolated_environment(
    source: Mapping[str, str],
    *,
    temp_dir: Path,
    uv_cache_dir: Path,
) -> dict[str, str]:
    """Remove source and Agent Factory settings from a child process environment."""

    environment = {
        key: value
        for key, value in source.items()
        if not key.startswith("AGENT_FACTORY_")
        and key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "TMPDIR": str(temp_dir),
            "UV_CACHE_DIR": str(uv_cache_dir),
        }
    )
    return environment


def _redact(value: str, sensitive_values: Sequence[str]) -> str:
    redacted = value
    for sensitive in sensitive_values:
        if sensitive:
            redacted = redacted.replace(sensitive, "[REDACTED]")
    return redacted


def assert_sensitive_values_absent(
    value: str,
    sensitive_values: Sequence[str],
    *,
    location: str,
) -> None:
    if any(sensitive and sensitive in value for sensitive in sensitive_values):
        raise SmokeFailure(f"sensitive value detected in {location}")


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: float,
    sensitive_values: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure(
            f"command could not complete: {Path(command[0]).name}"
        ) from exc
    combined = f"{completed.stdout}\n{completed.stderr}"
    assert_sensitive_values_absent(
        combined,
        sensitive_values,
        location=f"{Path(command[0]).name} output",
    )
    if completed.returncode != 0:
        detail = _redact(combined.strip(), sensitive_values)
        raise SmokeFailure(
            f"command failed with exit code {completed.returncode}: "
            f"{Path(command[0]).name}\n{detail}"
        )
    return completed


def _venv_python(environment_dir: Path) -> Path:
    if os.name == "nt":
        return environment_dir / "Scripts" / "python.exe"
    return environment_dir / "bin" / "python"


def _create_environment(
    *,
    uv: str,
    python: str,
    environment_dir: Path,
    requirements: Path,
    wheel_requirement: str,
    project_root: Path,
    process_environment: Mapping[str, str],
    timeout: float,
) -> Path:
    _run_checked(
        (uv, "venv", "--python", python, str(environment_dir)),
        cwd=project_root,
        environment=process_environment,
        timeout=timeout,
    )
    isolated_python = _venv_python(environment_dir)
    _run_checked(
        (
            uv,
            "pip",
            "install",
            "--python",
            str(isolated_python),
            "--requirement",
            str(requirements),
        ),
        cwd=project_root,
        environment=process_environment,
        timeout=timeout,
    )
    _run_checked(
        (
            uv,
            "pip",
            "install",
            "--python",
            str(isolated_python),
            "--no-deps",
            wheel_requirement,
        ),
        cwd=project_root,
        environment=process_environment,
        timeout=timeout,
    )
    return isolated_python


MINIMAL_IMPORT_PROBE: Final = """
import importlib.util
import json
import pathlib
import sys

import agent_factory
from agent_factory.sdk import AgentFactoryClient

module_path = pathlib.Path(agent_factory.__file__).resolve()
prefix = pathlib.Path(sys.prefix).resolve()
assert module_path.is_relative_to(prefix), (module_path, prefix)
assert importlib.util.find_spec("gradio") is None
assert importlib.util.find_spec("openai") is None
assert AgentFactoryClient.__module__ == "agent_factory.sdk.client"
print(json.dumps({"module_path": str(module_path), "prefix": str(prefix)}))
"""


OPTIONAL_IMPORT_PROBE: Final = """
from importlib.metadata import distribution
import pathlib
import sys

import agent_factory
import agent_factory.demo
import gradio
import openai

module_path = pathlib.Path(agent_factory.__file__).resolve()
assert module_path.is_relative_to(pathlib.Path(sys.prefix).resolve())
package = distribution("agent-factory")
extras = set(package.metadata.get_all("Provides-Extra") or ())
assert {"demo", "llm"} <= extras
assert any(
    item.name == "agent-factory-demo"
    and item.value == "agent_factory.demo:main"
    for item in package.entry_points
)
"""


SDK_PROBE: Final = """
import asyncio
import json
import os
import pathlib
import sys

import agent_factory
from agent_factory.sdk import AgentFactoryClient, RegisterPrototypeRequest

MODE = sys.argv[1]
BASE_URL = sys.argv[2]

async def main():
    module_path = pathlib.Path(agent_factory.__file__).resolve()
    assert module_path.is_relative_to(pathlib.Path(sys.prefix).resolve())
    async with AgentFactoryClient(
        base_url=BASE_URL,
        token=os.environ["AGENT_FACTORY_AUTH_TOKEN"],
        timeout=5.0,
    ) as client:
        readiness = await client.check_readiness()
        assert readiness.status == "ok"
        if MODE == "seed":
            request = RegisterPrototypeRequest.model_validate(
                {
                    "prototype_id": "local-alpha-smoke",
                    "version": "1.0.0",
                    "definition": {
                        "agent_type": "local-alpha-smoke-agent",
                        "role": "Local Alpha Smoke",
                        "system_prompt": "Return deterministic smoke evidence.",
                    },
                    "publish": True,
                }
            )
            prototype = await client.register_prototype(
                request,
                idempotency_key="local-alpha-smoke-register-v1",
            )
            assert prototype.prototype_id == "local-alpha-smoke"
            assert prototype.status.value == "published"
        elif MODE == "verify":
            page = await client.list_prototypes(
                agent_type="local-alpha-smoke-agent",
                page=1,
                page_size=10,
            )
            assert page.total == 1
            prototype = page.items[0]
            assert prototype.prototype_id == "local-alpha-smoke"
            assert prototype.version == "1.0.0"
            assert prototype.status.value == "published"
        else:
            raise AssertionError(f"unknown mode: {MODE}")
    print(json.dumps({"mode": MODE, "module_path": str(module_path)}))

asyncio.run(main())
"""


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _start_server(
    *,
    python: Path,
    cwd: Path,
    environment: Mapping[str, str],
    log_path: Path,
) -> ServerProcess:
    port = _available_port()
    command = (
        str(python),
        "-I",
        "-m",
        "uvicorn",
        "agent_factory.interfaces.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-access-log",
        "--log-level",
        "info",
    )
    creation_flags = 0
    start_new_session = False
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    with log_path.open("w", encoding="utf-8", newline="") as log:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creation_flags,
                start_new_session=start_new_session,
            )
        except OSError as exc:
            raise SmokeFailure("could not start isolated Uvicorn process") from exc
    return ServerProcess(process=process, port=port, log_path=log_path)


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SmokeFailure(f"could not read server log: {path.name}") from exc


def _wait_for_readiness(server: ServerProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = server.process.poll()
        if return_code is not None:
            log = _read_log(server.log_path)
            raise SmokeFailure(
                f"Uvicorn exited before readiness with code {return_code}\n{log}"
            )
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.port,
            timeout=min(1.0, timeout),
        )
        try:
            connection.request("GET", "/health/ready")
            response = connection.getresponse()
            body = response.read()
            if response.status == 200 and json.loads(body) == {"status": "ok"}:
                return
        except (OSError, TimeoutError, json.JSONDecodeError):
            pass
        finally:
            connection.close()
        time.sleep(0.2)
    raise SmokeFailure("Uvicorn did not become ready before timeout")


def _port_bind_failed(log: str) -> bool:
    normalized = log.lower()
    return any(
        marker in normalized
        for marker in (
            "address already in use",
            "only one usage of each socket address",
            "winerror 10048",
            "errno 98",
        )
    )


def _start_ready_server(
    *,
    python: Path,
    cwd: Path,
    environment: Mapping[str, str],
    log_prefix: Path,
    startup_timeout: float,
    shutdown_timeout: float,
    attempts: int = 3,
) -> ServerProcess:
    for attempt in range(1, attempts + 1):
        server = _start_server(
            python=python,
            cwd=cwd,
            environment=environment,
            log_path=log_prefix.with_name(f"{log_prefix.name}-{attempt}.log"),
        )
        try:
            _wait_for_readiness(server, startup_timeout)
        except SmokeFailure:
            log = _read_log(server.log_path)
            _force_stop(server, shutdown_timeout)
            if attempt < attempts and _port_bind_failed(log):
                continue
            raise
        except BaseException:
            _force_stop(server, shutdown_timeout)
            raise
        return server
    raise SmokeFailure("could not bind a loopback port after bounded retries")


def _stop_server(server: ServerProcess, timeout: float) -> None:
    if server.process.poll() is not None:
        if server.process.returncode != 0:
            raise SmokeFailure(
                f"Uvicorn exited unexpectedly with code {server.process.returncode}"
            )
        return
    try:
        if os.name == "nt":
            server.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            server.process.send_signal(signal.SIGTERM)
        return_code = server.process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        server.process.kill()
        server.process.wait(timeout=timeout)
        raise SmokeFailure("Uvicorn did not stop gracefully") from exc
    if not _graceful_shutdown_completed(
        return_code,
        _read_log(server.log_path),
        platform_name=os.name,
    ):
        raise SmokeFailure(f"Uvicorn graceful shutdown returned {return_code}")


def _graceful_shutdown_completed(
    return_code: int,
    log: str,
    *,
    platform_name: str,
) -> bool:
    if return_code == 0:
        return True
    # Uvicorn handles Windows SIGBREAK, completes its lifespan, then restores
    # and re-raises the signal. CPython reports that completed path as code 3.
    windows_markers = (
        "Application shutdown complete.",
        "Finished server process",
    )
    return (
        platform_name == "nt"
        and return_code == 3
        and all(marker in log for marker in windows_markers)
    )


def _force_stop(server: ServerProcess | None, timeout: float) -> None:
    if server is None or server.process.poll() is not None:
        return
    server.process.kill()
    try:
        server.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def _export_requirements(
    *,
    uv: str,
    project_root: Path,
    output: Path,
    process_environment: Mapping[str, str],
    extras: Sequence[str],
    timeout: float,
) -> None:
    command = [
        uv,
        "export",
        "--quiet",
        "--locked",
        "--no-dev",
        "--no-emit-project",
    ]
    for extra in extras:
        command.extend(("--extra", extra))
    command.extend(("--output-file", str(output)))
    _run_checked(
        command,
        cwd=project_root,
        environment=process_environment,
        timeout=timeout,
    )


def _migration_versions(database_path: Path) -> tuple[int, ...]:
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            rows = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
    except sqlite3.Error as exc:
        raise SmokeFailure("could not verify migration history") from exc
    return tuple(int(row[0]) for row in rows)


def _remove_run_directory(
    run_dir: Path,
    work_root: Path,
    *,
    timeout: float = 5.0,
) -> None:
    """Remove only one generated run directory, with bounded Windows retries."""

    try:
        relative = run_dir.resolve().relative_to(work_root.resolve())
    except ValueError as exc:
        raise SmokeFailure(
            "refusing to clean a run directory outside work root"
        ) from exc
    if len(relative.parts) != 1 or not relative.name.startswith("run-"):
        raise SmokeFailure("refusing to clean a non-run smoke directory")

    deadline = time.monotonic() + timeout
    while True:
        try:
            shutil.rmtree(run_dir)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise SmokeFailure(
                    "could not clean the smoke run directory before timeout"
                ) from exc
            time.sleep(0.2)


def _server_environment(
    process_environment: Mapping[str, str],
    *,
    token: str,
    database_path: Path,
    data_dir: Path,
) -> dict[str, str]:
    environment = dict(process_environment)
    environment.update(
        {
            "AGENT_FACTORY_ENVIRONMENT": "local-alpha-smoke",
            "AGENT_FACTORY_DATABASE_URL": (
                f"sqlite+aiosqlite:///{database_path.as_posix()}"
            ),
            "AGENT_FACTORY_DATA_DIR": str(data_dir),
            "AGENT_FACTORY_AUTH_TOKEN": token,
            "AGENT_FACTORY_AUTH_SUBJECT": "local-smoke-owner",
            "AGENT_FACTORY_AUTH_ROLES": '["admin"]',
        }
    )
    return environment


def _run_sdk_probe(
    *,
    python: Path,
    cwd: Path,
    environment: Mapping[str, str],
    server: ServerProcess,
    mode: str,
    token: str,
    timeout: float,
) -> None:
    _run_checked(
        (
            str(python),
            "-I",
            "-c",
            SDK_PROBE,
            mode,
            f"http://127.0.0.1:{server.port}",
        ),
        cwd=cwd,
        environment=environment,
        timeout=timeout,
        sensitive_values=(token,),
    )


def _build_and_verify(
    *,
    uv: str,
    project_root: Path,
    run_dir: Path,
    process_environment: Mapping[str, str],
    timeout: float,
) -> DistributionArtifacts:
    dist_dir = run_dir / "dist"
    _run_checked(
        (uv, "build", "--out-dir", str(dist_dir)),
        cwd=project_root,
        environment=process_environment,
        timeout=timeout,
    )
    return verify_distributions(*discover_distributions(dist_dir))


def run_smoke(
    *,
    project_root: Path,
    work_root: Path,
    uv_cache_dir: Path,
    python: str,
    command_timeout: float,
    startup_timeout: float,
    shutdown_timeout: float,
    keep_workdir: bool,
) -> SmokeSummary:
    """Execute the complete isolated release smoke and return stable evidence."""

    project_root = project_root.resolve()
    if (
        not (project_root / "pyproject.toml").is_file()
        or not (project_root / "uv.lock").is_file()
    ):
        raise SmokeFailure("project root must contain pyproject.toml and uv.lock")
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeFailure("uv executable was not found")

    work_root = work_root.resolve()
    uv_cache_dir = uv_cache_dir.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=work_root)).resolve()
    succeeded = False
    active_server: ServerProcess | None = None
    try:
        temp_dir = run_dir / "temp"
        runtime_dir = run_dir / "runtime"
        data_dir = run_dir / "data"
        for directory in (temp_dir, runtime_dir, data_dir):
            directory.mkdir()
        process_environment = isolated_environment(
            os.environ,
            temp_dir=temp_dir,
            uv_cache_dir=uv_cache_dir,
        )

        print("[1/6] Building and validating fresh distributions")
        artifacts = _build_and_verify(
            uv=uv,
            project_root=project_root,
            run_dir=run_dir,
            process_environment=process_environment,
            timeout=command_timeout,
        )

        print("[2/6] Installing the minimal wheel environment from uv.lock")
        minimal_requirements = run_dir / "minimal-requirements.txt"
        _export_requirements(
            uv=uv,
            project_root=project_root,
            output=minimal_requirements,
            process_environment=process_environment,
            extras=(),
            timeout=command_timeout,
        )
        minimal_python = _create_environment(
            uv=uv,
            python=python,
            environment_dir=run_dir / "minimal-env",
            requirements=minimal_requirements,
            wheel_requirement=str(artifacts.wheel),
            project_root=project_root,
            process_environment=process_environment,
            timeout=command_timeout,
        )
        _run_checked(
            (str(minimal_python), "-I", "-c", MINIMAL_IMPORT_PROBE),
            cwd=runtime_dir,
            environment=process_environment,
            timeout=command_timeout,
        )

        token = secrets.token_urlsafe(48)
        database_path = data_dir / "agent-factory.db"
        server_environment = _server_environment(
            process_environment,
            token=token,
            database_path=database_path,
            data_dir=data_dir,
        )

        print("[3/6] Starting wheel-only Uvicorn and seeding through the SDK")
        active_server = _start_ready_server(
            python=minimal_python,
            cwd=runtime_dir,
            environment=server_environment,
            log_prefix=run_dir / "server-first",
            startup_timeout=startup_timeout,
            shutdown_timeout=shutdown_timeout,
        )
        _run_sdk_probe(
            python=minimal_python,
            cwd=runtime_dir,
            environment=server_environment,
            server=active_server,
            mode="seed",
            token=token,
            timeout=command_timeout,
        )
        _stop_server(active_server, shutdown_timeout)
        assert_sensitive_values_absent(
            _read_log(active_server.log_path),
            (token,),
            location="first Uvicorn log",
        )
        active_server = None

        print("[4/6] Restarting against the same SQLite and verifying recovery")
        active_server = _start_ready_server(
            python=minimal_python,
            cwd=runtime_dir,
            environment=server_environment,
            log_prefix=run_dir / "server-restart",
            startup_timeout=startup_timeout,
            shutdown_timeout=shutdown_timeout,
        )
        _run_sdk_probe(
            python=minimal_python,
            cwd=runtime_dir,
            environment=server_environment,
            server=active_server,
            mode="verify",
            token=token,
            timeout=command_timeout,
        )
        _stop_server(active_server, shutdown_timeout)
        assert_sensitive_values_absent(
            _read_log(active_server.log_path),
            (token,),
            location="restarted Uvicorn log",
        )
        active_server = None

        versions = _migration_versions(database_path)
        if versions != tuple(range(1, 7)):
            raise SmokeFailure(f"expected migration versions 1-6, found {versions!r}")

        print("[5/6] Installing and validating demo and llm extras")
        optional_requirements = run_dir / "optional-requirements.txt"
        _export_requirements(
            uv=uv,
            project_root=project_root,
            output=optional_requirements,
            process_environment=process_environment,
            extras=("demo", "llm"),
            timeout=command_timeout,
        )
        optional_python = _create_environment(
            uv=uv,
            python=python,
            environment_dir=run_dir / "optional-env",
            requirements=optional_requirements,
            wheel_requirement=f"{artifacts.wheel}[demo,llm]",
            project_root=project_root,
            process_environment=process_environment,
            timeout=command_timeout,
        )
        _run_checked(
            (str(optional_python), "-I", "-c", OPTIONAL_IMPORT_PROBE),
            cwd=runtime_dir,
            environment=process_environment,
            timeout=command_timeout,
        )

        print("[6/6] Local Alpha wheel smoke passed")
        summary = SmokeSummary(
            version=artifacts.version,
            wheel_name=artifacts.wheel.name,
            migration_versions=versions,
            prototype_id=PROTOTYPE_ID,
        )
        succeeded = True
        return summary
    finally:
        _force_stop(active_server, shutdown_timeout)
        if succeeded and not keep_workdir:
            _remove_run_directory(run_dir, work_root)
        elif keep_workdir or not succeeded:
            print(f"Smoke work directory retained at: {run_dir}")


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and smoke-test Agent Factory from isolated wheels.",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--work-root",
        type=Path,
        default=PROJECT_ROOT / ".tmp" / "local-alpha-smoke",
    )
    parser.add_argument(
        "--uv-cache-dir",
        type=Path,
        default=PROJECT_ROOT / ".tmp" / "uv-cache",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--command-timeout", type=_positive_float, default=180.0)
    parser.add_argument("--startup-timeout", type=_positive_float, default=20.0)
    parser.add_argument("--shutdown-timeout", type=_positive_float, default=10.0)
    parser.add_argument("--keep-workdir", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_smoke(
            project_root=args.project_root,
            work_root=args.work_root,
            uv_cache_dir=args.uv_cache_dir,
            python=args.python,
            command_timeout=args.command_timeout,
            startup_timeout=args.startup_timeout,
            shutdown_timeout=args.shutdown_timeout,
            keep_workdir=args.keep_workdir,
        )
    except SmokeFailure as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: "
        f"{summary.wheel_name}; migrations={summary.migration_versions}; "
        f"prototype={summary.prototype_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

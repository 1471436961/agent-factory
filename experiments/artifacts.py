"""Canonical, write-once local artifacts for recoverable M5 execution."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agent_factory.domain.common import canonical_json_bytes

_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_LISTED_ARTIFACTS = 10_000
_ModelT = TypeVar("_ModelT", bound=BaseModel)
PublishHook = Callable[[Path, Path], None]


class ArtifactStoreError(RuntimeError):
    """Base failure for local experiment artifact operations."""


class ArtifactConflictError(ArtifactStoreError):
    """An immutable artifact identity already contains different bytes."""


class ArtifactCorruptionError(ArtifactStoreError):
    """A stored artifact is unreadable, invalid, or non-canonical."""


class ArtifactStore:
    """Publish complete files exactly once within one local filesystem root."""

    def __init__(
        self,
        root: Path,
        *,
        before_publish: PublishHook | None = None,
    ) -> None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._root = root.resolve(strict=True)
        except OSError as exc:
            raise ArtifactStoreError("artifact root cannot be prepared") from exc
        if not self._root.is_dir():
            raise ArtifactStoreError("artifact root must be a directory")
        self._before_publish = before_publish

    @property
    def root(self) -> Path:
        return self._root

    def write_model_once(self, relative_path: str, model: BaseModel) -> bool:
        """Publish canonical model bytes; return false for an identical replay."""

        return self.write_bytes_once(relative_path, canonical_model_bytes(model))

    def write_bytes_once(self, relative_path: str, content: bytes) -> bool:
        """Publish bytes without ever replacing an existing target name."""

        if not content or len(content) > _MAX_ARTIFACT_BYTES:
            raise ArtifactStoreError("artifact size is invalid")
        target = self._target(relative_path, create_parent=True)
        if target.is_symlink():
            raise ArtifactStoreError("artifact target cannot be a symbolic link")
        descriptor = -1
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if self._before_publish is not None:
                self._before_publish(temporary_path, target)
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                return self._verify_replay(target, content)
            except OSError as exc:
                raise ArtifactStoreError(
                    "artifact cannot be published atomically"
                ) from exc
            return True
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def read_model(self, relative_path: str, model_type: type[_ModelT]) -> _ModelT:
        """Read a canonical JSON artifact into its strict Pydantic contract."""

        path = self._target(relative_path, create_parent=False)
        content = self._read_bytes(path)
        try:
            raw = json.loads(content)
            model = model_type.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ArtifactCorruptionError("artifact model is invalid") from exc
        if content != canonical_model_bytes(model):
            raise ArtifactCorruptionError("artifact JSON is not canonical")
        return model

    def read_bytes(self, relative_path: str) -> bytes:
        """Read one bounded artifact without interpreting its content."""

        return self._read_bytes(self._target(relative_path, create_parent=False))

    def exists(self, relative_path: str) -> bool:
        """Return whether a regular, in-root artifact exists."""

        path = self._target(relative_path, create_parent=False)
        if path.is_symlink():
            raise ArtifactStoreError("artifact target cannot be a symbolic link")
        return path.is_file()

    def list_files(self, relative_prefix: str) -> tuple[str, ...]:
        """List bounded regular files below one in-root directory."""

        directory = self._target(relative_prefix, create_parent=False)
        if not directory.exists():
            return ()
        if directory.is_symlink():
            raise ArtifactStoreError("artifact directory cannot be a symbolic link")
        if not directory.is_dir():
            raise ArtifactStoreError("artifact prefix must be a directory")

        files: list[str] = []
        pending = [directory]
        try:
            while pending:
                current = pending.pop()
                for child in sorted(current.iterdir(), key=lambda item: item.name):
                    if child.is_symlink():
                        raise ArtifactStoreError(
                            "artifact tree cannot contain symbolic links"
                        )
                    if child.is_dir():
                        pending.append(child)
                        continue
                    if not child.is_file():
                        raise ArtifactStoreError(
                            "artifact tree contains a non-regular entry"
                        )
                    files.append(child.relative_to(self._root).as_posix())
                    if len(files) > _MAX_LISTED_ARTIFACTS:
                        raise ArtifactStoreError("artifact listing exceeds file limit")
        except ArtifactStoreError:
            raise
        except OSError as exc:
            raise ArtifactStoreError("artifact tree cannot be listed") from exc
        return tuple(sorted(files))

    def _verify_replay(self, target: Path, expected: bytes) -> bool:
        if target.is_symlink():
            raise ArtifactStoreError("artifact target cannot be a symbolic link")
        actual = self._read_bytes(target)
        if actual != expected:
            raise ArtifactConflictError(
                "artifact identity already contains other bytes"
            )
        return False

    def _target(self, relative_path: str, *, create_parent: bool) -> Path:
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or any(
            part in {"", ".", ".."} for part in candidate.parts
        ):
            raise ArtifactStoreError("artifact path must be a clean relative path")
        unresolved = self._root.joinpath(*candidate.parts)
        try:
            if create_parent:
                unresolved.parent.mkdir(parents=True, exist_ok=True)
            if unresolved.parent.exists():
                parent = unresolved.parent.resolve(strict=True)
            else:
                nearest = unresolved.parent
                while not nearest.exists():
                    nearest = nearest.parent
                resolved_nearest = nearest.resolve(strict=True)
                if not resolved_nearest.is_relative_to(self._root):
                    raise ArtifactStoreError("artifact path escapes storage root")
                return unresolved
        except OSError as exc:
            raise ArtifactStoreError("artifact parent cannot be resolved") from exc
        if not parent.is_relative_to(self._root):
            raise ArtifactStoreError("artifact path escapes storage root")
        return parent / unresolved.name

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        if path.is_symlink():
            raise ArtifactStoreError("artifact target cannot be a symbolic link")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ArtifactStoreError("artifact cannot be inspected") from exc
        if size <= 0 or size > _MAX_ARTIFACT_BYTES:
            raise ArtifactCorruptionError("artifact size is invalid")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ArtifactStoreError("artifact cannot be read") from exc


def canonical_model_bytes(model: BaseModel) -> bytes:
    """Serialize one model in the repository's canonical JSON representation."""

    return (
        canonical_json_bytes(model.model_dump(mode="json", exclude_none=False)) + b"\n"
    )

"""Write-once artifact publication, replay, and corruption tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from experiments.artifacts import (
    ArtifactConflictError,
    ArtifactCorruptionError,
    ArtifactStore,
    ArtifactStoreError,
)
from experiments.contracts import MatcherKind, TextMatcher


def test_model_artifact_is_canonical_readable_and_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    model = TextMatcher(kind=MatcherKind.EXACT, pattern="current value")

    assert store.write_model_once("models/matcher.json", model) is True
    assert store.write_model_once("models/matcher.json", model) is False
    assert store.read_model("models/matcher.json", TextMatcher) == model

    with pytest.raises(ArtifactConflictError, match="other bytes"):
        store.write_model_once(
            "models/matcher.json",
            TextMatcher(kind=MatcherKind.EXACT, pattern="different value"),
        )


def test_artifact_store_rejects_noncanonical_or_invalid_models(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    target = store.root / "matcher.json"
    target.write_text(
        '{ "kind": "exact", "pattern": "value", "case_sensitive": false }\n',
        encoding="utf-8",
    )
    with pytest.raises(ArtifactCorruptionError, match="not canonical"):
        store.read_model("matcher.json", TextMatcher)

    target.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ArtifactCorruptionError, match="model is invalid"):
        store.read_model("matcher.json", TextMatcher)


def test_artifact_store_rejects_escape_missing_and_invalid_size(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ArtifactStoreError, match="clean relative"):
        store.write_bytes_once("../escape.json", b"{}\n")
    with pytest.raises(ArtifactStoreError, match="cannot be inspected"):
        store.read_bytes("missing.json")
    with pytest.raises(ArtifactStoreError, match="size is invalid"):
        store.write_bytes_once("empty.json", b"")
    with pytest.raises(ArtifactStoreError, match="size is invalid"):
        store.write_bytes_once("oversized.json", b"x" * (2 * 1024 * 1024 + 1))
    assert store.exists("not-created/result.json") is False


def test_artifact_store_rejects_file_root(tmp_path: Path) -> None:
    root = tmp_path / "root-file"
    root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ArtifactStoreError, match="cannot be prepared"):
        ArtifactStore(root)


def test_publish_os_failure_is_normalized_and_temp_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    def deny_link(_source: Path, _target: Path) -> None:
        raise PermissionError("injected")

    monkeypatch.setattr("experiments.artifacts.os.link", deny_link)
    with pytest.raises(ArtifactStoreError, match="published atomically"):
        store.write_bytes_once("result.json", b"{}\n")
    assert list(store.root.glob("*.tmp")) == []


def test_interruption_before_publish_leaves_no_visible_artifact(tmp_path: Path) -> None:
    def interrupt(_temporary: Path, _target: Path) -> None:
        raise RuntimeError("injected interruption")

    store = ArtifactStore(tmp_path / "artifacts", before_publish=interrupt)
    with pytest.raises(RuntimeError, match="injected interruption"):
        store.write_bytes_once("runs/run.json", b"{}\n")

    assert not (store.root / "runs" / "run.json").exists()
    assert list((store.root / "runs").glob("*.tmp")) == []


def test_concurrent_identical_publish_creates_one_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    def publish() -> bool:
        return store.write_bytes_once("shared/result.json", b'{"ok":true}\n')

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: publish(), range(16)))

    assert results.count(True) == 1
    assert results.count(False) == 15
    assert store.read_bytes("shared/result.json") == b'{"ok":true}\n'


def test_symbolic_link_target_is_rejected_when_supported(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    link = store.root / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable for this Windows account")

    with pytest.raises(ArtifactStoreError, match="symbolic link"):
        store.read_bytes("linked.json")

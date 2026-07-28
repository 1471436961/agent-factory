"""Deterministic identity seal for externally retained Pilot evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from agent_factory.domain.common import sha256_model
from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import (
    ExecutionPlan,
    PilotEvidenceArtifact,
    PilotEvidenceSeal,
    PilotEvidenceStatusCount,
)
from experiments.evidence import ExperimentEvidenceError, ExperimentEvidenceLoader
from experiments.loader import LoadedExperimentDataset

_MAX_EVIDENCE_FILE_BYTES = 2 * 1024 * 1024
_MAX_EVIDENCE_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_EVIDENCE_FILES = 10_000
_MAX_SEAL_BYTES = 2 * 1024 * 1024


class PilotEvidenceSealError(RuntimeError):
    """Pilot evidence cannot be validated or represented by one stable identity."""


def build_pilot_evidence_seal(
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    evidence_root: Path,
    evidence_root_label: str,
    freeze_manifest_checksum: str,
    expected_execution_manifest_checksum: str,
) -> PilotEvidenceSeal:
    """Validate the journal, then hash every regular file below its external root."""

    root = _resolve_evidence_root(evidence_root)
    try:
        evidence = ExperimentEvidenceLoader(
            dataset=dataset,
            plan=plan,
            store=ArtifactStore(root),
        ).load()
    except ExperimentEvidenceError as exc:
        raise PilotEvidenceSealError("Pilot evidence journal is invalid") from exc
    if evidence.manifest.manifest_checksum != expected_execution_manifest_checksum:
        raise PilotEvidenceSealError(
            "Pilot execution Manifest does not match its freeze Manifest"
        )

    files = _read_evidence_inventory(root)
    status_counts = Counter(run.status for run in evidence.runs)
    unsigned = PilotEvidenceSeal(
        experiment_id=dataset.definition.experiment_id,
        evidence_root_label=evidence_root_label,
        freeze_manifest_checksum=freeze_manifest_checksum,
        execution_manifest_checksum=evidence.manifest.manifest_checksum,
        plan_checksum=plan.plan_checksum,
        run_count=len(evidence.runs),
        attempt_count=sum(len(run.attempts) for run in evidence.runs),
        status_counts=tuple(
            PilotEvidenceStatusCount(status=status, count=count)
            for status, count in sorted(status_counts.items(), key=lambda item: item[0])
        ),
        files=files,
        total_bytes=sum(item.byte_size for item in files),
        seal_checksum="0" * 64,
    )
    return unsigned.model_copy(
        update={"seal_checksum": calculate_pilot_evidence_seal_checksum(unsigned)}
    )


def verify_pilot_evidence_seal(
    seal: PilotEvidenceSeal,
    *,
    dataset: LoadedExperimentDataset,
    plan: ExecutionPlan,
    evidence_root: Path,
) -> None:
    """Rebuild a seal from retained bytes and require exact identity equality."""

    rebuilt = build_pilot_evidence_seal(
        dataset=dataset,
        plan=plan,
        evidence_root=evidence_root,
        evidence_root_label=seal.evidence_root_label,
        freeze_manifest_checksum=seal.freeze_manifest_checksum,
        expected_execution_manifest_checksum=seal.execution_manifest_checksum,
    )
    if rebuilt != seal:
        raise PilotEvidenceSealError("Pilot evidence differs from its committed seal")


def calculate_pilot_evidence_seal_checksum(seal: PilotEvidenceSeal) -> str:
    return sha256_model(seal, exclude={"seal_checksum"})


def load_pilot_evidence_seal(path: Path) -> PilotEvidenceSeal:
    """Load bounded canonical seal bytes and verify the embedded checksum."""

    try:
        content = path.read_bytes()
    except OSError as exc:
        raise PilotEvidenceSealError("Pilot evidence seal cannot be read") from exc
    if not content or len(content) > _MAX_SEAL_BYTES:
        raise PilotEvidenceSealError("Pilot evidence seal size is invalid")
    try:
        seal = PilotEvidenceSeal.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise PilotEvidenceSealError("Pilot evidence seal is invalid") from exc
    if content != canonical_model_bytes(seal):
        raise PilotEvidenceSealError("Pilot evidence seal is not canonical JSON")
    if seal.seal_checksum != calculate_pilot_evidence_seal_checksum(seal):
        raise PilotEvidenceSealError("Pilot evidence seal checksum mismatch")
    return seal


def publish_pilot_evidence_seal(seal: PilotEvidenceSeal, output_path: Path) -> bool:
    """Publish the canonical seal once; an identical replay is harmless."""

    output = output_path.resolve(strict=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    return ArtifactStore(output.parent).write_model_once(output.name, seal)


def _resolve_evidence_root(evidence_root: Path) -> Path:
    if evidence_root.is_symlink():
        raise PilotEvidenceSealError("Pilot evidence root cannot be a symbolic link")
    try:
        root = evidence_root.resolve(strict=True)
    except OSError as exc:
        raise PilotEvidenceSealError("Pilot evidence root cannot be resolved") from exc
    if not root.is_dir():
        raise PilotEvidenceSealError("Pilot evidence root must be a directory")
    return root


def _read_evidence_inventory(root: Path) -> tuple[PilotEvidenceArtifact, ...]:
    files: list[PilotEvidenceArtifact] = []
    total_bytes = 0
    pending = [root]
    try:
        while pending:
            current = pending.pop()
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                if child.is_symlink():
                    raise PilotEvidenceSealError(
                        "Pilot evidence tree cannot contain symbolic links"
                    )
                if child.is_dir():
                    pending.append(child)
                    continue
                if not child.is_file():
                    raise PilotEvidenceSealError(
                        "Pilot evidence tree contains a non-regular entry"
                    )
                content = child.read_bytes()
                if not content or len(content) > _MAX_EVIDENCE_FILE_BYTES:
                    raise PilotEvidenceSealError("Pilot evidence file size is invalid")
                total_bytes += len(content)
                if total_bytes > _MAX_EVIDENCE_TOTAL_BYTES:
                    raise PilotEvidenceSealError("Pilot evidence tree is too large")
                files.append(
                    PilotEvidenceArtifact(
                        path=child.relative_to(root).as_posix(),
                        byte_size=len(content),
                        content_checksum=hashlib.sha256(content).hexdigest(),
                    )
                )
                if len(files) > _MAX_EVIDENCE_FILES:
                    raise PilotEvidenceSealError(
                        "Pilot evidence file count is too large"
                    )
    except OSError as exc:
        raise PilotEvidenceSealError("Pilot evidence tree cannot be read") from exc
    return tuple(sorted(files, key=lambda item: item.path))

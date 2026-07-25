"""Deterministic rendering and write-once analysis package tests."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_factory.domain.common import sha256_model
from experiments.artifacts import (
    ArtifactConflictError,
    ArtifactStore,
    canonical_model_bytes,
)
from experiments.contracts import (
    AnalysisArtifactManifest,
    AnalysisConfig,
    AnalysisPopulation,
    AnalysisSummary,
    ConfidenceInterval,
    ExperimentCondition,
    ExperimentScenario,
    HypothesisDecision,
    HypothesisName,
    HypothesisResult,
    TaskConditionAggregate,
)
from experiments.reporting import (
    AnalysisReportCorruptionError,
    AnalysisReportPublisher,
    render_markdown_report,
    render_metrics_csv,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _interval(lower: float, upper: float) -> ConfidenceInterval:
    return ConfidenceInterval(
        lower=lower,
        upper=upper,
        requested_replicates=100,
        valid_replicates=100,
        invalid_replicates=0,
    )


def _aggregate(
    *,
    population: AnalysisPopulation,
    condition: ExperimentCondition,
) -> TaskConditionAggregate:
    if condition is ExperimentCondition.MANUAL:
        if population is AnalysisPopulation.INTENTION_TO_TREAT:
            return TaskConditionAggregate(
                task_id="report-task",
                domain_id="report-domain",
                scenario=ExperimentScenario.ADAPTATION,
                condition=condition,
                population=population,
                planned_runs=5,
                included_runs=5,
                succeeded_runs=4,
                schema_passes=3,
                required_facts_total=15,
                required_facts_covered=12,
                personalization_total=10,
                personalization_satisfied=8,
                schema_pass_rate=0.6,
                omission_rate=0.2,
                adaptation_rate=0.8,
            )
        return TaskConditionAggregate(
            task_id="report-task",
            domain_id="report-domain",
            scenario=ExperimentScenario.ADAPTATION,
            condition=condition,
            population=population,
            planned_runs=5,
            included_runs=4,
            succeeded_runs=4,
            schema_passes=3,
            required_facts_total=12,
            required_facts_covered=10,
            personalization_total=8,
            personalization_satisfied=7,
            schema_pass_rate=0.75,
            omission_rate=0.166666666667,
            adaptation_rate=0.875,
        )
    return TaskConditionAggregate(
        task_id="report-task",
        domain_id="report-domain",
        scenario=ExperimentScenario.ADAPTATION,
        condition=condition,
        population=population,
        planned_runs=5,
        included_runs=5,
        succeeded_runs=5,
        schema_passes=5,
        required_facts_total=15,
        required_facts_covered=15,
        personalization_total=10,
        personalization_satisfied=9,
        schema_pass_rate=1,
        omission_rate=0,
        adaptation_rate=0.9,
    )


def _hypotheses() -> tuple[HypothesisResult, ...]:
    primary = (
        HypothesisResult(
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            paired_task_count=1,
            effect_estimate=0.4,
            confidence_interval=_interval(0.2, 0.6),
            decision=HypothesisDecision.SUPPORTED,
        ),
        HypothesisResult(
            hypothesis=HypothesisName.H2_KNOWLEDGE_OMISSION,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            paired_task_count=1,
            effect_estimate=1,
            confidence_interval=_interval(0.5, 1),
            absolute_difference=0.2,
            absolute_difference_interval=_interval(0.1, 0.3),
            decision=HypothesisDecision.SUPPORTED,
        ),
        HypothesisResult(
            hypothesis=HypothesisName.H4_PERSONALIZATION,
            population=AnalysisPopulation.INTENTION_TO_TREAT,
            paired_task_count=1,
            effect_estimate=0.1,
            confidence_interval=_interval(0, 0.2),
            decision=HypothesisDecision.SUPPORTED,
        ),
    )
    sensitivity = (
        HypothesisResult(
            hypothesis=HypothesisName.H1_SCHEMA_CONSISTENCY,
            population=AnalysisPopulation.SUCCEEDED_ONLY,
            paired_task_count=1,
            effect_estimate=0.25,
            confidence_interval=_interval(0.1, 0.4),
            decision=HypothesisDecision.NOT_EVALUATED,
        ),
        HypothesisResult(
            hypothesis=HypothesisName.H2_KNOWLEDGE_OMISSION,
            population=AnalysisPopulation.SUCCEEDED_ONLY,
            paired_task_count=1,
            effect_estimate=1,
            confidence_interval=_interval(0.5, 1),
            absolute_difference=0.166666666667,
            absolute_difference_interval=_interval(0.1, 0.25),
            decision=HypothesisDecision.NOT_EVALUATED,
        ),
        HypothesisResult(
            hypothesis=HypothesisName.H4_PERSONALIZATION,
            population=AnalysisPopulation.SUCCEEDED_ONLY,
            paired_task_count=1,
            effect_estimate=0.025,
            confidence_interval=_interval(-0.05, 0.1),
            decision=HypothesisDecision.NOT_EVALUATED,
        ),
    )
    return (*primary, *sensitivity)


@pytest.fixture
def summary() -> AnalysisSummary:
    config = AnalysisConfig(bootstrap_seed=20260725, bootstrap_iterations=100)
    return AnalysisSummary(
        experiment_id="report-experiment",
        dataset_checksum=SHA_A,
        definition_checksum=SHA_B,
        plan_checksum=SHA_C,
        score_set_checksum=SHA_D,
        task_count=1,
        repetitions=5,
        config=config,
        config_checksum=sha256_model(config),
        aggregates=tuple(
            _aggregate(population=population, condition=condition)
            for population in AnalysisPopulation
            for condition in ExperimentCondition
        ),
        hypotheses=_hypotheses(),
    )


def _package_root(store: ArtifactStore, summary: AnalysisSummary) -> Path:
    return store.root / "analysis" / summary.experiment_id / sha256_model(summary)


def test_publishes_verifies_and_idempotently_replays_complete_package(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = AnalysisReportPublisher(store)

    manifest = publisher.publish(summary)
    replay = publisher.publish(summary)

    assert replay == manifest
    assert publisher.verify(summary.experiment_id, sha256_model(summary)) == summary
    package = _package_root(store, summary)
    assert {item.name for item in package.iterdir()} == {
        "summary.json",
        "metrics.csv",
        "report.md",
        "artifact-manifest.json",
    }
    for reference in manifest.files:
        content = (package / reference.path).read_bytes()
        assert len(content) == reference.byte_size
        assert hashlib.sha256(content).hexdigest() == reference.content_checksum


def test_csv_and_markdown_are_stable_machine_and_presentation_views(
    summary: AnalysisSummary,
) -> None:
    metrics = render_metrics_csv(summary)
    report = render_markdown_report(summary)
    rows = list(csv.DictReader(io.StringIO(metrics.decode("utf-8"))))

    assert len(rows) == 4
    assert [row["population"] for row in rows] == [
        "intention-to-treat",
        "intention-to-treat",
        "succeeded-only",
        "succeeded-only",
    ]
    assert [row["condition"] for row in rows] == [
        "manual-agent",
        "factory-agent",
        "manual-agent",
        "factory-agent",
    ]
    assert rows[0]["failed_runs"] == "1"
    assert rows[0]["omission_rate"] == "0.2"
    decoded_report = report.decode("utf-8")
    assert "Evidence boundary" in decoded_report
    assert sha256_model(summary) in decoded_report
    assert "`supported`" in decoded_report
    assert "Absolute omission delta (95% CI)" in decoded_report
    assert "raw model responses" in decoded_report
    assert report.endswith(b"\n")


def test_report_renders_the_frozen_twenty_four_task_coordinate_scale(
    summary: AnalysisSummary,
) -> None:
    aggregates: list[TaskConditionAggregate] = []
    for index in range(1, 25):
        scenario = (
            ExperimentScenario.CONSISTENCY
            if index <= 12
            else ExperimentScenario.ADAPTATION
        )
        for template in summary.aggregates:
            payload = template.model_dump(mode="python")
            payload["task_id"] = f"report-task-{index:02d}"
            payload["domain_id"] = f"report-domain-{index:02d}"
            payload["scenario"] = scenario
            if scenario is ExperimentScenario.CONSISTENCY:
                payload["personalization_total"] = 0
                payload["personalization_satisfied"] = 0
                payload["adaptation_rate"] = None
            aggregates.append(TaskConditionAggregate.model_validate(payload))
    hypothesis_payloads = []
    for result in summary.hypotheses:
        payload = result.model_dump(mode="python")
        payload["paired_task_count"] = (
            12 if result.hypothesis is HypothesisName.H4_PERSONALIZATION else 24
        )
        hypothesis_payloads.append(payload)
    payload = summary.model_dump(mode="python")
    payload["task_count"] = 24
    payload["aggregates"] = aggregates
    payload["hypotheses"] = hypothesis_payloads
    expanded = AnalysisSummary.model_validate(payload)

    rows = list(
        csv.DictReader(io.StringIO(render_metrics_csv(expanded).decode("utf-8")))
    )
    report = render_markdown_report(expanded).decode("utf-8")

    assert len(rows) == 96
    assert all(
        row["adaptation_rate"] == "" for row in rows if row["scenario"] == "consistency"
    )
    assert "| `manual-agent` | 120 | 96 | 24 |" in report
    assert "| `factory-agent` | 120 | 120 | 0 |" in report


def test_manifest_is_last_and_interrupted_publication_resumes(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    def interrupt_manifest(_temporary: Path, target: Path) -> None:
        if target.name == "artifact-manifest.json":
            raise RuntimeError("injected manifest interruption")

    interrupted_store = ArtifactStore(
        tmp_path / "artifacts",
        before_publish=interrupt_manifest,
    )
    with pytest.raises(RuntimeError, match="manifest interruption"):
        AnalysisReportPublisher(interrupted_store).publish(summary)

    package = _package_root(interrupted_store, summary)
    assert (package / "summary.json").is_file()
    assert (package / "metrics.csv").is_file()
    assert (package / "report.md").is_file()
    assert not (package / "artifact-manifest.json").exists()

    resumed = AnalysisReportPublisher(ArtifactStore(interrupted_store.root))
    manifest = resumed.publish(summary)
    assert manifest.analysis_checksum == sha256_model(summary)
    assert resumed.verify(summary.experiment_id, sha256_model(summary)) == summary


def test_publish_rejects_conflicting_bytes_at_same_analysis_identity(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    package = _package_root(store, summary)
    relative = package.relative_to(store.root).as_posix()
    store.write_bytes_once(f"{relative}/summary.json", b"{}\n")

    with pytest.raises(ArtifactConflictError, match="other bytes"):
        AnalysisReportPublisher(store).publish(summary)


def test_verifier_rejects_digest_corruption(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = AnalysisReportPublisher(store)
    publisher.publish(summary)
    package = _package_root(store, summary)
    (package / "report.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(AnalysisReportCorruptionError, match="digest mismatch"):
        publisher.verify(summary.experiment_id, sha256_model(summary))


def test_verifier_rerenders_presentation_even_if_manifest_was_rewritten(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = AnalysisReportPublisher(store)
    manifest = publisher.publish(summary)
    package = _package_root(store, summary)
    changed_report = b"changed but checksum-aligned\n"
    (package / "report.md").write_bytes(changed_report)
    files = tuple(
        item.model_copy(
            update={
                "content_checksum": hashlib.sha256(changed_report).hexdigest(),
                "byte_size": len(changed_report),
            }
        )
        if item.path == "report.md"
        else item
        for item in manifest.files
    )
    rewritten_manifest = manifest.model_copy(update={"files": files})
    (package / "artifact-manifest.json").write_bytes(
        canonical_model_bytes(rewritten_manifest)
    )

    with pytest.raises(AnalysisReportCorruptionError, match="does not reproduce"):
        publisher.verify(summary.experiment_id, sha256_model(summary))


def test_verifier_rejects_manifest_identity_rewritten_in_place(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = AnalysisReportPublisher(store)
    manifest = publisher.publish(summary)
    package = _package_root(store, summary)
    rewritten = manifest.model_copy(update={"experiment_id": "other-experiment"})
    (package / "artifact-manifest.json").write_bytes(canonical_model_bytes(rewritten))

    with pytest.raises(AnalysisReportCorruptionError, match="identity"):
        publisher.verify(summary.experiment_id, sha256_model(summary))


def test_verifier_rejects_checksum_aligned_summary_under_wrong_identity(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    publisher = AnalysisReportPublisher(store)
    manifest = publisher.publish(summary)
    package = _package_root(store, summary)
    changed_config = summary.config.model_copy(update={"bootstrap_seed": 7})
    changed_summary = summary.model_copy(
        update={
            "config": changed_config,
            "config_checksum": sha256_model(changed_config),
        }
    )
    changed_bytes = canonical_model_bytes(changed_summary)
    (package / "summary.json").write_bytes(changed_bytes)
    files = tuple(
        item.model_copy(
            update={
                "content_checksum": hashlib.sha256(changed_bytes).hexdigest(),
                "byte_size": len(changed_bytes),
            }
        )
        if item.path == "summary.json"
        else item
        for item in manifest.files
    )
    rewritten_manifest = manifest.model_copy(update={"files": files})
    (package / "artifact-manifest.json").write_bytes(
        canonical_model_bytes(rewritten_manifest)
    )

    with pytest.raises(AnalysisReportCorruptionError, match="summary checksum"):
        publisher.verify(summary.experiment_id, sha256_model(summary))


def test_report_displays_undefined_relative_and_absolute_intervals(
    summary: AnalysisSummary,
) -> None:
    hypotheses = list(summary.hypotheses)
    index = next(
        index
        for index, item in enumerate(hypotheses)
        if item.population is AnalysisPopulation.SUCCEEDED_ONLY
        and item.hypothesis is HypothesisName.H2_KNOWLEDGE_OMISSION
    )
    payload = hypotheses[index].model_dump(mode="python")
    payload["effect_estimate"] = None
    payload["confidence_interval"] = ConfidenceInterval(
        requested_replicates=100,
        valid_replicates=0,
        invalid_replicates=100,
    )
    payload["absolute_difference_interval"] = ConfidenceInterval(
        requested_replicates=100,
        valid_replicates=0,
        invalid_replicates=100,
    )
    hypotheses[index] = HypothesisResult.model_validate(payload)
    changed = summary.model_copy(update={"hypotheses": tuple(hypotheses)})

    report = render_markdown_report(changed).decode("utf-8")

    assert "| `h2-knowledge-omission` | 1 | N/A | N/A | 0.166666666667 N/A |" in report


def test_manifest_contract_rejects_noncanonical_file_layout(
    tmp_path: Path,
    summary: AnalysisSummary,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    manifest = AnalysisReportPublisher(store).publish(summary)
    payload = manifest.model_dump(mode="python")
    payload["files"] = list(reversed(payload["files"]))

    with pytest.raises(ValidationError, match="canonical order"):
        AnalysisArtifactManifest.model_validate(payload)

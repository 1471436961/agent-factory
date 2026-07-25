"""Deterministic rendering and manifest-last publication of M5 analysis reports."""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping

from agent_factory.domain.common import sha256_model
from experiments.artifacts import ArtifactStore, canonical_model_bytes
from experiments.contracts import (
    AnalysisArtifactManifest,
    AnalysisPopulation,
    AnalysisSummary,
    ExperimentCondition,
    HypothesisName,
    HypothesisResult,
    TaskConditionAggregate,
)

_SUMMARY_PATH = "summary.json"
_METRICS_PATH = "metrics.csv"
_REPORT_PATH = "report.md"
_MANIFEST_PATH = "artifact-manifest.json"
_POPULATION_ORDER = {value: index for index, value in enumerate(AnalysisPopulation)}
_CONDITION_ORDER = {value: index for index, value in enumerate(ExperimentCondition)}
_HYPOTHESIS_ORDER = {value: index for index, value in enumerate(HypothesisName)}
_CSV_COLUMNS = (
    "population",
    "task_id",
    "domain_id",
    "scenario",
    "condition",
    "planned_runs",
    "included_runs",
    "succeeded_runs",
    "failed_runs",
    "schema_passes",
    "schema_pass_rate",
    "required_facts_total",
    "required_facts_covered",
    "omission_rate",
    "personalization_total",
    "personalization_satisfied",
    "adaptation_rate",
)


class AnalysisReportCorruptionError(RuntimeError):
    """A published analysis package is incomplete or internally inconsistent."""


class AnalysisReportPublisher:
    """Publish and verify one immutable, content-addressed analysis package."""

    def __init__(self, store: ArtifactStore) -> None:
        self._store = store

    def publish(self, summary: AnalysisSummary) -> AnalysisArtifactManifest:
        """Publish data files first and the package manifest as the commit marker."""

        analysis_checksum = sha256_model(summary)
        payloads = _render_payloads(summary)
        manifest = _build_manifest(summary, analysis_checksum, payloads)
        package_root = _package_root(summary.experiment_id, analysis_checksum)
        for path in (_SUMMARY_PATH, _METRICS_PATH, _REPORT_PATH):
            self._store.write_bytes_once(
                f"{package_root}/{path}",
                payloads[path],
            )
        self._store.write_model_once(
            f"{package_root}/{_MANIFEST_PATH}",
            manifest,
        )
        self.verify(summary.experiment_id, analysis_checksum)
        return manifest

    def verify(
        self,
        experiment_id: str,
        analysis_checksum: str,
    ) -> AnalysisSummary:
        """Verify the commit marker, every digest, and deterministic re-rendering."""

        package_root = _package_root(experiment_id, analysis_checksum)
        manifest = self._store.read_model(
            f"{package_root}/{_MANIFEST_PATH}",
            AnalysisArtifactManifest,
        )
        if (
            manifest.experiment_id != experiment_id
            or manifest.analysis_checksum != analysis_checksum
        ):
            raise AnalysisReportCorruptionError(
                "analysis manifest identity does not match package path"
            )
        files = {item.path: item for item in manifest.files}
        payloads = {
            path: self._store.read_bytes(f"{package_root}/{path}")
            for path in (_SUMMARY_PATH, _METRICS_PATH, _REPORT_PATH)
        }
        for path, content in payloads.items():
            reference = files[path]
            if len(content) != reference.byte_size or _sha256(content) != (
                reference.content_checksum
            ):
                raise AnalysisReportCorruptionError(
                    f"analysis artifact digest mismatch: {path}"
                )
        summary = self._store.read_model(
            f"{package_root}/{_SUMMARY_PATH}",
            AnalysisSummary,
        )
        if sha256_model(summary) != analysis_checksum:
            raise AnalysisReportCorruptionError(
                "analysis summary checksum does not match package identity"
            )
        expected = _render_payloads(summary)
        if any(payloads[path] != expected[path] for path in expected):
            raise AnalysisReportCorruptionError(
                "analysis presentation does not reproduce from summary"
            )
        return summary


def render_metrics_csv(summary: AnalysisSummary) -> bytes:
    """Render one stable aggregate row per population, task, and condition."""

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(_CSV_COLUMNS)
    for aggregate in sorted(summary.aggregates, key=_aggregate_sort_key):
        writer.writerow(
            (
                aggregate.population.value,
                aggregate.task_id,
                aggregate.domain_id,
                aggregate.scenario.value,
                aggregate.condition.value,
                aggregate.planned_runs,
                aggregate.included_runs,
                aggregate.succeeded_runs,
                aggregate.planned_runs - aggregate.succeeded_runs,
                aggregate.schema_passes,
                _format_csv_number(aggregate.schema_pass_rate),
                aggregate.required_facts_total,
                aggregate.required_facts_covered,
                _format_csv_number(aggregate.omission_rate),
                aggregate.personalization_total,
                aggregate.personalization_satisfied,
                _format_csv_number(aggregate.adaptation_rate),
            )
        )
    return stream.getvalue().encode("utf-8")


def render_markdown_report(summary: AnalysisSummary) -> bytes:
    """Render a concise report whose numeric facts come only from the summary."""

    analysis_checksum = sha256_model(summary)
    itt_aggregates = [
        item
        for item in summary.aggregates
        if item.population is AnalysisPopulation.INTENTION_TO_TREAT
    ]
    condition_counts = _condition_counts(itt_aggregates)
    hypotheses = sorted(summary.hypotheses, key=_hypothesis_sort_key)
    primary = [
        item
        for item in hypotheses
        if item.population is AnalysisPopulation.INTENTION_TO_TREAT
    ]
    sensitivity = [
        item
        for item in hypotheses
        if item.population is AnalysisPopulation.SUCCEEDED_ONLY
    ]
    lines = [
        "# M5 Writer Experiment Analysis Report",
        "",
        (
            "> Evidence boundary: this report is rendered deterministically from "
            "`AnalysisSummary`. Conclusions apply only to its bound dataset, plan, "
            "score set, and analysis config. Formal status also requires review of "
            "the M5.5 manifest, source commit, provider, model, SDK, and price "
            "snapshot."
        ),
        "",
        "## Analysis Identity",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| Experiment | `{summary.experiment_id}` |",
        f"| Analysis checksum | `{analysis_checksum}` |",
        f"| Dataset checksum | `{summary.dataset_checksum}` |",
        f"| Definition checksum | `{summary.definition_checksum}` |",
        f"| Plan checksum | `{summary.plan_checksum}` |",
        f"| Score set checksum | `{summary.score_set_checksum}` |",
        f"| Analysis config checksum | `{summary.config_checksum}` |",
        f"| Tasks / repetitions | {summary.task_count} / {summary.repetitions} |",
        (
            f"| Bootstrap | {summary.config.bootstrap_iterations} iterations; "
            f"seed={summary.config.bootstrap_seed}; "
            f"confidence={_format_number(summary.config.confidence_level)} |"
        ),
        "",
        "## Execution Completeness",
        "",
        "| Condition | Planned | Succeeded | Failed |",
        "| --- | ---: | ---: | ---: |",
    ]
    for condition in ExperimentCondition:
        planned, succeeded = condition_counts[condition]
        lines.append(
            f"| `{condition.value}` | {planned} | {succeeded} | {planned - succeeded} |"
        )
    lines.extend(
        [
            "",
            "## Primary Analysis: Intention-to-treat",
            "",
            (
                "Execution failures use the preregistered worst-case mapping. H1/H4 "
                "effects are `FACTORY - MANUAL`; H2 is relative omission reduction."
            ),
            "",
            *_hypothesis_table(primary),
            "",
            "## Succeeded-only Sensitivity Analysis",
            "",
            (
                "This population only examines the influence of execution failures. "
                "Its `decision` is `not-evaluated` and cannot replace primary analysis."
            ),
            "",
            *_hypothesis_table(sensitivity),
            "",
            "## Reproduction Boundaries",
            "",
            (
                "- `summary.json` is the machine source of truth; `metrics.csv` and "
                "this report must reproduce from it byte for byte."
            ),
            (
                "- This report excludes raw model responses, prompt bodies, "
                "credentials, and human ratings."
            ),
            (
                "- Local checksums detect accidental corruption but cannot prevent a "
                "filesystem administrator from rewriting every artifact."
            ),
            (
                "- Without the formal frozen manifest, this report must not be "
                "presented as a formal model experiment result."
            ),
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _render_payloads(summary: AnalysisSummary) -> dict[str, bytes]:
    return {
        _SUMMARY_PATH: canonical_model_bytes(summary),
        _METRICS_PATH: render_metrics_csv(summary),
        _REPORT_PATH: render_markdown_report(summary),
    }


def _build_manifest(
    summary: AnalysisSummary,
    analysis_checksum: str,
    payloads: Mapping[str, bytes],
) -> AnalysisArtifactManifest:
    media_types = {
        _SUMMARY_PATH: "application/json",
        _METRICS_PATH: "text/csv",
        _REPORT_PATH: "text/markdown",
    }
    return AnalysisArtifactManifest.model_validate(
        {
            "experiment_id": summary.experiment_id,
            "analysis_checksum": analysis_checksum,
            "files": [
                {
                    "path": path,
                    "media_type": media_types[path],
                    "content_checksum": _sha256(payloads[path]),
                    "byte_size": len(payloads[path]),
                }
                for path in (_SUMMARY_PATH, _METRICS_PATH, _REPORT_PATH)
            ],
        }
    )


def _package_root(experiment_id: str, analysis_checksum: str) -> str:
    return f"analysis/{experiment_id}/{analysis_checksum}"


def _aggregate_sort_key(
    aggregate: TaskConditionAggregate,
) -> tuple[int, str, int]:
    return (
        _POPULATION_ORDER[aggregate.population],
        aggregate.task_id,
        _CONDITION_ORDER[aggregate.condition],
    )


def _hypothesis_sort_key(result: HypothesisResult) -> tuple[int, int]:
    return (
        _POPULATION_ORDER[result.population],
        _HYPOTHESIS_ORDER[result.hypothesis],
    )


def _condition_counts(
    aggregates: list[TaskConditionAggregate],
) -> dict[ExperimentCondition, tuple[int, int]]:
    return {
        condition: (
            sum(
                item.planned_runs for item in aggregates if item.condition is condition
            ),
            sum(
                item.succeeded_runs
                for item in aggregates
                if item.condition is condition
            ),
        )
        for condition in ExperimentCondition
    }


def _hypothesis_table(results: list[HypothesisResult]) -> list[str]:
    lines = [
        (
            "| Hypothesis | Paired tasks | Effect | 95% CI | "
            "Absolute omission delta (95% CI) | Decision |"
        ),
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in results:
        interval = result.confidence_interval
        bounds = (
            "N/A"
            if interval.lower is None or interval.upper is None
            else f"[{_format_number(interval.lower)}, {_format_number(interval.upper)}]"
        )
        absolute = _absolute_effect(result)
        lines.append(
            "| "
            f"`{result.hypothesis.value}` | {result.paired_task_count} | "
            f"{_format_number(result.effect_estimate)} | {bounds} | {absolute} | "
            f"`{result.decision.value}` |"
        )
    return lines


def _format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value == 0:
        return "0"
    return format(value, ".12f").rstrip("0").rstrip(".")


def _format_csv_number(value: float | None) -> str:
    return "" if value is None else _format_number(value)


def _absolute_effect(result: HypothesisResult) -> str:
    interval = result.absolute_difference_interval
    if result.absolute_difference is None or interval is None:
        return "N/A"
    if interval.lower is None or interval.upper is None:
        bounds = "N/A"
    else:
        bounds = f"[{_format_number(interval.lower)}, {_format_number(interval.upper)}]"
    return f"{_format_number(result.absolute_difference)} {bounds}"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

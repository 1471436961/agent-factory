"""One-command offline replay from execution journals to analysis reports."""

from __future__ import annotations

from dataclasses import dataclass

from agent_factory.domain.common import sha256_model
from experiments.analysis import ExperimentAnalyzer
from experiments.artifacts import ArtifactStore
from experiments.contracts import (
    AnalysisArtifactManifest,
    AnalysisConfig,
    AnalysisSummary,
    ExecutionManifest,
    ExecutionPlan,
    ScoreArtifactManifest,
)
from experiments.evidence import ExperimentEvidenceLoader
from experiments.loader import LoadedExperimentDataset
from experiments.reporting import AnalysisReportPublisher
from experiments.score_artifacts import ScoreArtifactPublisher
from experiments.scoring import DeterministicScorer


class OfflineAnalysisError(RuntimeError):
    """The derived evidence chain became inconsistent during replay."""


@dataclass(frozen=True, slots=True)
class OfflineAnalysisResult:
    """Checksummed commit markers returned by one completed replay."""

    execution_manifest: ExecutionManifest
    score_manifest: ScoreArtifactManifest
    analysis_summary: AnalysisSummary
    analysis_manifest: AnalysisArtifactManifest


class OfflineAnalysisPipeline:
    """Validate raw evidence, score, analyze, and publish without model calls."""

    def __init__(
        self,
        *,
        dataset: LoadedExperimentDataset,
        plan: ExecutionPlan,
        run_store: ArtifactStore,
        output_store: ArtifactStore,
        config: AnalysisConfig | None = None,
    ) -> None:
        self._dataset = dataset
        self._plan = plan
        self._evidence_loader = ExperimentEvidenceLoader(
            dataset=dataset,
            plan=plan,
            store=run_store,
        )
        self._scorer = DeterministicScorer(dataset)
        self._score_publisher = ScoreArtifactPublisher(output_store)
        self._analyzer = ExperimentAnalyzer(dataset, plan, config)
        self._report_publisher = AnalysisReportPublisher(output_store)

    def run(self) -> OfflineAnalysisResult:
        """Replay a complete journal into immutable derived evidence packages."""

        evidence = self._evidence_loader.load()
        scores = tuple(self._scorer.score(run) for run in evidence.runs)
        score_manifest = self._score_publisher.publish(
            execution_manifest=evidence.manifest,
            dataset_checksum=self._dataset.dataset_checksum,
            scores=scores,
        )
        persisted_scores = self._score_publisher.verify(
            evidence.manifest.experiment_id,
            evidence.manifest.manifest_checksum,
        )
        if len(persisted_scores) != len(evidence.runs) or any(
            score.run_id != run.run_id or score.run_checksum != sha256_model(run)
            for run, score in zip(evidence.runs, persisted_scores, strict=True)
        ):
            raise OfflineAnalysisError(
                "persisted scores do not match terminal run evidence"
            )
        summary = self._analyzer.analyze(persisted_scores)
        if summary.score_set_checksum != score_manifest.score_set_checksum:
            raise OfflineAnalysisError(
                "analysis summary does not bind the published score package"
            )
        analysis_manifest = self._report_publisher.publish(summary)
        return OfflineAnalysisResult(
            execution_manifest=evidence.manifest,
            score_manifest=score_manifest,
            analysis_summary=summary,
            analysis_manifest=analysis_manifest,
        )

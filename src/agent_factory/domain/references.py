"""Versioned references shared by M2 governance snapshots."""

from agent_factory.domain.common import FrozenModel, SemVer, Sha256, Slug


class SkillTreeRef(FrozenModel):
    """Immutable identity of a registered skill-tree version."""

    tree_id: Slug
    version: SemVer
    checksum: Sha256


class EvaluationSuiteRef(FrozenModel):
    """Immutable identity of a registered evaluation-suite version."""

    suite_id: Slug
    version: SemVer
    checksum: Sha256

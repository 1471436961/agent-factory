"""Tests for M2 evaluation contracts and deterministic rule execution."""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
import regex
from pydantic import ValidationError

from agent_factory.domain.common import sha256_model
from agent_factory.domain.enums import EvaluationDecision, RuleKind
from agent_factory.domain.errors import (
    EvaluationRuleTimeoutError,
    EvaluationSubmissionError,
    EvaluationSuiteMismatchError,
)
from agent_factory.domain.evaluation import (
    EvaluationCase,
    EvaluationReport,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    RuleResult,
    SubmittedCaseResult,
)
from agent_factory.domain.references import EvaluationSuiteRef, SkillTreeRef
from agent_factory.domain.services.evaluation import DeterministicRuleEngine

CHECKSUM = "a" * 64
INSTANCE_ID = UUID("00000000-0000-0000-0000-000000000001")


def _rule(
    rule_id: str,
    kind: RuleKind,
    parameters: dict[str, object],
    *,
    hard: bool = True,
    weight: float = 1.0,
) -> EvaluationRule:
    return EvaluationRule(
        rule_id=rule_id,
        kind=kind,
        parameters=parameters,
        hard=hard,
        weight=weight,
    )


def _suite(
    *,
    fixed_now: datetime,
    rules: tuple[EvaluationRule, ...],
    cases: tuple[EvaluationCase, ...] | None = None,
    minimum_soft_score: float = 0.8,
    require_manual_review: bool = False,
) -> EvaluationSuite:
    draft = EvaluationSuiteDraft(
        suite_id="writer-suite",
        version="1.0.0",
        rules=rules,
        cases=cases or (EvaluationCase(case_id="case-one", input="Write a document."),),
        minimum_soft_score=minimum_soft_score,
        require_manual_review=require_manual_review,
    )
    return EvaluationSuite.model_validate(
        {
            **draft.model_dump(mode="python"),
            "checksum": sha256_model(draft),
            "created_at": fixed_now,
            "created_by": "owner",
        }
    )


def _submission(
    suite: EvaluationSuite,
    *results: SubmittedCaseResult,
) -> EvaluationSubmission:
    return EvaluationSubmission(
        instance_id=INSTANCE_ID,
        instance_revision=1,
        suite=EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        ),
        runtime_model="fixture-runtime",
        case_results=results,
    )


@pytest.mark.parametrize(
    ("kind", "parameters", "message"),
    [
        (RuleKind.REQUIRED_TERMS, {"terms": []}, "between 1 and"),
        (
            RuleKind.REQUIRED_TERMS,
            {"terms": ["agent", "agent"]},
            "must be unique",
        ),
        (
            RuleKind.FORBIDDEN_TERMS,
            {"terms": ["bad"], "case_sensitive": "yes"},
            "must be a boolean",
        ),
        (
            RuleKind.JSON_SCHEMA,
            {"schema": {"type": "invalid"}},
            "not valid Draft",
        ),
        (RuleKind.REGEX, {"pattern": "("}, "not a valid regular expression"),
        (RuleKind.MAX_LENGTH, {"max_chars": True}, "must be an integer"),
        (RuleKind.TOOL_CALLED, {"tool_name": "INVALID"}, "valid slug"),
        (
            RuleKind.MAX_LENGTH,
            {"max_chars": 10, "unknown": 1},
            "unknown rule parameters",
        ),
    ],
)
def test_evaluation_rule_rejects_invalid_kind_parameters(
    kind: RuleKind,
    parameters: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _rule("quality-rule", kind, parameters)


def test_suite_submission_and_called_tools_are_canonical_and_unique(
    fixed_now: datetime,
) -> None:
    first_rule = _rule(
        "required-rule",
        RuleKind.REQUIRED_TERMS,
        {"terms": ["agent"]},
    )
    second_rule = _rule(
        "length-rule",
        RuleKind.MAX_LENGTH,
        {"max_chars": 20},
    )
    suite = _suite(
        fixed_now=fixed_now,
        rules=(first_rule, second_rule),
        cases=(
            EvaluationCase(case_id="case-two", input="Two"),
            EvaluationCase(case_id="case-one", input="One"),
        ),
    )
    assert [rule.rule_id for rule in suite.rules] == [
        "length-rule",
        "required-rule",
    ]
    assert [case.case_id for case in suite.cases] == ["case-one", "case-two"]

    with pytest.raises(ValidationError, match="called_tools contains duplicate"):
        SubmittedCaseResult(
            case_id="case-one",
            output_text="Agent",
            called_tools=("document-search", "document-search"),
        )
    with pytest.raises(ValidationError, match="submitted case ids must be unique"):
        _submission(
            suite,
            SubmittedCaseResult(case_id="case-one", output_text="Agent"),
            SubmittedCaseResult(case_id="case-one", output_text="Agent"),
        )


def test_rule_engine_executes_all_rule_kinds_and_hashes_case_evidence(
    fixed_now: datetime,
) -> None:
    rules = (
        _rule(
            "schema-rule",
            RuleKind.JSON_SCHEMA,
            {
                "schema": {
                    "type": "object",
                    "required": ["title"],
                    "properties": {"title": {"type": "string"}},
                }
            },
        ),
        _rule(
            "required-rule",
            RuleKind.REQUIRED_TERMS,
            {"terms": ["agent factory"]},
        ),
        _rule(
            "forbidden-rule",
            RuleKind.FORBIDDEN_TERMS,
            {"terms": ["guaranteed"]},
        ),
        _rule("regex-rule", RuleKind.REGEX, {"pattern": r"Agent\s+Factory"}),
        _rule(
            "length-rule",
            RuleKind.MAX_LENGTH,
            {"max_chars": 100},
            hard=False,
        ),
        _rule(
            "tool-rule",
            RuleKind.TOOL_CALLED,
            {"tool_name": "document-search"},
            hard=False,
        ),
    )
    suite = _suite(fixed_now=fixed_now, rules=rules)
    result = SubmittedCaseResult(
        case_id="case-one",
        output_text="Agent Factory builds governed agents.",
        structured_output={"title": "Agent Factory"},
        called_tools=("document-search",),
    )

    outcome = DeterministicRuleEngine().evaluate(
        suite=suite,
        submission=_submission(suite, result),
    )

    assert outcome.decision is EvaluationDecision.PASS
    assert outcome.hard_rules_passed is True
    assert outcome.soft_score == 1.0
    assert all(item.passed for item in outcome.rule_results)
    assert outcome.case_results[0].checksum == sha256_model(result)


def test_rule_engine_applies_hard_soft_and_manual_review_decision_order(
    fixed_now: datetime,
) -> None:
    hard_suite = _suite(
        fixed_now=fixed_now,
        rules=(
            _rule(
                "required-rule",
                RuleKind.REQUIRED_TERMS,
                {"terms": ["missing"]},
            ),
        ),
    )
    hard_outcome = DeterministicRuleEngine().evaluate(
        suite=hard_suite,
        submission=_submission(
            hard_suite,
            SubmittedCaseResult(case_id="case-one", output_text="Agent"),
        ),
    )
    assert hard_outcome.decision is EvaluationDecision.FAIL
    assert hard_outcome.hard_rules_passed is False

    soft_suite = _suite(
        fixed_now=fixed_now,
        rules=(
            _rule(
                "passing-rule",
                RuleKind.REQUIRED_TERMS,
                {"terms": ["agent"]},
                hard=False,
                weight=1,
            ),
            _rule(
                "failing-rule",
                RuleKind.REQUIRED_TERMS,
                {"terms": ["missing"]},
                hard=False,
                weight=3,
            ),
        ),
        minimum_soft_score=0.5,
    )
    soft_outcome = DeterministicRuleEngine().evaluate(
        suite=soft_suite,
        submission=_submission(
            soft_suite,
            SubmittedCaseResult(case_id="case-one", output_text="Agent"),
        ),
    )
    assert soft_outcome.soft_score == 0.25
    assert soft_outcome.decision is EvaluationDecision.FAIL

    review_suite = _suite(
        fixed_now=fixed_now,
        rules=(
            _rule(
                "passing-rule",
                RuleKind.REQUIRED_TERMS,
                {"terms": ["agent"]},
            ),
        ),
        require_manual_review=True,
    )
    review_outcome = DeterministicRuleEngine().evaluate(
        suite=review_suite,
        submission=_submission(
            review_suite,
            SubmittedCaseResult(case_id="case-one", output_text="Agent"),
        ),
    )
    assert review_outcome.decision is EvaluationDecision.REVIEW_REQUIRED
    assert review_outcome.soft_score == 1.0


def test_rule_engine_rejects_suite_and_case_set_mismatch(
    fixed_now: datetime,
) -> None:
    suite = _suite(
        fixed_now=fixed_now,
        rules=(_rule("length-rule", RuleKind.MAX_LENGTH, {"max_chars": 10}),),
    )
    valid_result = SubmittedCaseResult(case_id="case-one", output_text="Agent")
    wrong_suite = _submission(suite, valid_result).model_copy(
        update={
            "suite": EvaluationSuiteRef(
                suite_id=suite.suite_id,
                version=suite.version,
                checksum="b" * 64,
            )
        }
    )
    with pytest.raises(EvaluationSuiteMismatchError):
        DeterministicRuleEngine().evaluate(suite=suite, submission=wrong_suite)

    wrong_case = EvaluationSubmission(
        instance_id=INSTANCE_ID,
        instance_revision=1,
        suite=_submission(suite, valid_result).suite,
        runtime_model="fixture-runtime",
        case_results=(
            SubmittedCaseResult(case_id="unexpected-case", output_text="Agent"),
        ),
    )
    with pytest.raises(EvaluationSubmissionError) as exc_info:
        DeterministicRuleEngine().evaluate(suite=suite, submission=wrong_case)
    assert exc_info.value.details["missing_cases"] == ("case-one",)
    assert exc_info.value.details["unexpected_cases"] == ("unexpected-case",)


def test_rule_engine_handles_schema_absence_case_sensitivity_and_forbidden_terms(
    fixed_now: datetime,
) -> None:
    suite = _suite(
        fixed_now=fixed_now,
        rules=(
            _rule(
                "schema-rule",
                RuleKind.JSON_SCHEMA,
                {"schema": {"type": "object"}},
                hard=False,
            ),
            _rule(
                "case-rule",
                RuleKind.REQUIRED_TERMS,
                {"terms": ["Agent"], "case_sensitive": True},
                hard=False,
            ),
            _rule(
                "forbidden-rule",
                RuleKind.FORBIDDEN_TERMS,
                {"terms": ["unsafe"]},
                hard=False,
            ),
        ),
        minimum_soft_score=1.0,
    )
    outcome = DeterministicRuleEngine().evaluate(
        suite=suite,
        submission=_submission(
            suite,
            SubmittedCaseResult(
                case_id="case-one",
                output_text="agent unsafe",
            ),
        ),
    )
    assert outcome.soft_score == 0
    assert outcome.decision is EvaluationDecision.FAIL


def test_regex_timeout_aborts_evaluation(
    fixed_now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = _suite(
        fixed_now=fixed_now,
        rules=(_rule("regex-rule", RuleKind.REGEX, {"pattern": "agent"}),),
    )

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(regex, "search", raise_timeout)
    with pytest.raises(EvaluationRuleTimeoutError):
        DeterministicRuleEngine().evaluate(
            suite=suite,
            submission=_submission(
                suite,
                SubmittedCaseResult(case_id="case-one", output_text="agent"),
            ),
        )


def test_report_and_evidence_models_reject_inconsistent_snapshots(
    fixed_now: datetime,
) -> None:
    result = RuleResult(
        rule_id="required-rule",
        case_id="case-one",
        passed=True,
        score=1,
    )
    base: dict[str, object] = {
        "case_results": (
            {
                "case_id": "case-one",
                "checksum": CHECKSUM,
            },
        ),
        "rule_results": (result,),
        "hard_rules_passed": True,
        "soft_score": 1,
        "decision": EvaluationDecision.PASS,
        "report_id": UUID("00000000-0000-0000-0000-000000000002"),
        "instance_id": INSTANCE_ID,
        "instance_revision": 1,
        "agent_spec_checksum": CHECKSUM,
        "skill_tree": SkillTreeRef(
            tree_id="writer-skills",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        "suite": EvaluationSuiteRef(
            suite_id="writer-suite",
            version="1.0.0",
            checksum=CHECKSUM,
        ),
        "runtime_model": "fixture-runtime",
        "started_at": fixed_now,
        "completed_at": fixed_now,
    }
    report = EvaluationReport.model_validate(base)
    assert report.decision is EvaluationDecision.PASS
    case_results = base["case_results"]
    assert isinstance(case_results, tuple)

    with pytest.raises(ValidationError, match="cannot precede"):
        EvaluationReport.model_validate(
            {
                **base,
                "completed_at": fixed_now - timedelta(seconds=1),
            }
        )
    with pytest.raises(ValidationError, match="require all hard"):
        EvaluationReport.model_validate({**base, "hard_rules_passed": False})
    with pytest.raises(ValidationError, match="references must be unique"):
        EvaluationReport.model_validate({**base, "case_results": case_results * 2})
    with pytest.raises(ValidationError, match="unique per rule and case"):
        EvaluationReport.model_validate({**base, "rule_results": (result, result)})
    with pytest.raises(ValidationError, match="unknown cases"):
        EvaluationReport.model_validate(
            {
                **base,
                "rule_results": (
                    result.model_copy(update={"case_id": "unknown-case"}),
                ),
            }
        )
    with pytest.raises(ValidationError, match="must not exceed"):
        RuleResult(
            rule_id="required-rule",
            case_id="case-one",
            passed=False,
            score=0,
            evidence={"raw": "x" * 5_000},
        )

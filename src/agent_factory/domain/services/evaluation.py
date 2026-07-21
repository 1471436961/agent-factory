"""Deterministic evaluation of submitted case evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

import regex
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from agent_factory.domain.common import (
    FrozenJsonObject,
    canonical_json_bytes,
    sha256_model,
)
from agent_factory.domain.enums import EvaluationDecision, RuleKind
from agent_factory.domain.errors import (
    EvaluationRuleTimeoutError,
    EvaluationSubmissionError,
    EvaluationSuiteMismatchError,
)
from agent_factory.domain.evaluation import (
    CaseResultRef,
    EvaluationOutcome,
    EvaluationRule,
    EvaluationSubmission,
    EvaluationSuite,
    EvaluationSuiteDraft,
    RuleResult,
    SubmittedCaseResult,
)
from agent_factory.domain.references import EvaluationSuiteRef

REGEX_TIMEOUT_SECONDS = 0.05


def checksum_evaluation_suite(
    suite: EvaluationSuite | EvaluationSuiteDraft,
) -> str:
    """Hash only the immutable suite definition, excluding registry metadata."""

    excluded = (
        {"checksum", "created_at", "created_by"}
        if isinstance(suite, EvaluationSuite)
        else set()
    )
    payload = suite.model_dump(mode="json", exclude=excluded, exclude_none=False)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class DeterministicRuleEngine:
    """Apply a registered suite to caller-supplied evidence without I/O."""

    def evaluate(
        self,
        *,
        suite: EvaluationSuite,
        submission: EvaluationSubmission,
    ) -> EvaluationOutcome:
        expected_suite = EvaluationSuiteRef(
            suite_id=suite.suite_id,
            version=suite.version,
            checksum=suite.checksum,
        )
        if submission.suite != expected_suite:
            raise EvaluationSuiteMismatchError(
                details={
                    "expected": expected_suite.model_dump(mode="json"),
                    "actual": submission.suite.model_dump(mode="json"),
                }
            )

        expected_case_ids = {case.case_id for case in suite.cases}
        submitted_case_ids = {result.case_id for result in submission.case_results}
        if submitted_case_ids != expected_case_ids:
            raise EvaluationSubmissionError(
                details={
                    "missing_cases": sorted(expected_case_ids - submitted_case_ids),
                    "unexpected_cases": sorted(submitted_case_ids - expected_case_ids),
                }
            )

        results_by_case = {result.case_id: result for result in submission.case_results}
        case_refs = tuple(
            CaseResultRef(
                case_id=case.case_id,
                checksum=sha256_model(results_by_case[case.case_id]),
                artifact_uri=results_by_case[case.case_id].artifact_uri,
            )
            for case in suite.cases
        )
        rule_results = tuple(
            self._evaluate_rule(
                rule=rule,
                result=results_by_case[case.case_id],
            )
            for case in suite.cases
            for rule in suite.rules
        )

        rules_by_id = {rule.rule_id: rule for rule in suite.rules}
        hard_rules_passed = all(
            result.passed for result in rule_results if rules_by_id[result.rule_id].hard
        )
        soft_results = tuple(
            result for result in rule_results if not rules_by_id[result.rule_id].hard
        )
        if soft_results:
            weighted_score = sum(
                result.score * rules_by_id[result.rule_id].weight
                for result in soft_results
            )
            total_weight = sum(
                rules_by_id[result.rule_id].weight for result in soft_results
            )
            soft_score = weighted_score / total_weight
        else:
            soft_score = 1.0

        if not hard_rules_passed or soft_score < suite.minimum_soft_score:
            decision = EvaluationDecision.FAIL
        elif suite.require_manual_review:
            decision = EvaluationDecision.REVIEW_REQUIRED
        else:
            decision = EvaluationDecision.PASS

        return EvaluationOutcome(
            case_results=case_refs,
            rule_results=rule_results,
            hard_rules_passed=hard_rules_passed,
            soft_score=soft_score,
            decision=decision,
        )

    def _evaluate_rule(
        self,
        *,
        rule: EvaluationRule,
        result: SubmittedCaseResult,
    ) -> RuleResult:
        passed, evidence = _run_rule(rule, result)
        return RuleResult(
            rule_id=rule.rule_id,
            case_id=result.case_id,
            passed=passed,
            score=1.0 if passed else 0.0,
            evidence=evidence,
        )


def _run_rule(
    rule: EvaluationRule,
    result: SubmittedCaseResult,
) -> tuple[bool, Mapping[str, object]]:
    parameters = rule.parameters

    if rule.kind is RuleKind.JSON_SCHEMA:
        schema = parameters["schema"]
        assert isinstance(schema, Mapping)
        if result.structured_output is None:
            return False, {"structured_output_present": False}
        validator = Draft202012Validator(FrozenJsonObject(schema).to_builtin())
        output = FrozenJsonObject(result.structured_output).to_builtin()
        first_error = next(validator.iter_errors(output), None)
        if first_error is None:
            return True, {"structured_output_present": True, "valid": True}
        return False, {
            "structured_output_present": True,
            "valid": False,
            "validator": str(first_error.validator),
        }

    if rule.kind in {RuleKind.REQUIRED_TERMS, RuleKind.FORBIDDEN_TERMS}:
        raw_terms = parameters["terms"]
        assert isinstance(raw_terms, tuple)
        terms = tuple(str(term) for term in raw_terms)
        case_sensitive = bool(parameters.get("case_sensitive", False))
        haystack = (
            result.output_text if case_sensitive else result.output_text.casefold()
        )
        compared_terms = (
            terms if case_sensitive else tuple(term.casefold() for term in terms)
        )
        present = tuple(
            original
            for original, compared in zip(terms, compared_terms, strict=True)
            if compared in haystack
        )
        if rule.kind is RuleKind.REQUIRED_TERMS:
            missing = tuple(term for term in terms if term not in present)
            return not missing, {
                "case_sensitive": case_sensitive,
                "missing_terms": missing,
                "missing_count": len(missing),
            }
        return not present, {
            "case_sensitive": case_sensitive,
            "present_forbidden_terms": present,
            "present_count": len(present),
        }

    if rule.kind is RuleKind.REGEX:
        pattern = parameters["pattern"]
        assert isinstance(pattern, str)
        try:
            matched = (
                regex.search(
                    pattern,
                    result.output_text,
                    flags=regex.VERSION1,
                    timeout=REGEX_TIMEOUT_SECONDS,
                )
                is not None
            )
        except TimeoutError as exc:
            raise EvaluationRuleTimeoutError(
                details={"rule_id": rule.rule_id, "case_id": result.case_id}
            ) from exc
        return matched, {"matched": matched}

    if rule.kind is RuleKind.MAX_LENGTH:
        maximum = parameters["max_chars"]
        assert isinstance(maximum, int)
        actual = len(result.output_text)
        return actual <= maximum, {
            "actual_chars": actual,
            "max_chars": maximum,
        }

    if rule.kind is RuleKind.TOOL_CALLED:
        tool_name = parameters["tool_name"]
        assert isinstance(tool_name, str)
        called = tool_name in result.called_tools
        return called, {"tool_name": tool_name, "called": called}

    raise AssertionError(f"unsupported validated rule kind: {rule.kind}")

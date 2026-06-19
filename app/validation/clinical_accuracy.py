"""Clinical-review accuracy metrics for the auto-decided safety target."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.review_result import CriterionStatus, Recommendation, ReviewResult
from app.models.safety import SafetyGateStatus
from app.review.engine import ClinicalReviewEngine
from app.review.evaluation import _coerce_to_review_result


DEFAULT_AUTO_DECISION_THRESHOLD = 0.99
SAFETY_CRITICAL_SLICE_NAMES = ("tb", "contraindication", "specialist", "step_therapy")


@dataclass(frozen=True)
class CalibrationBucket:
    """Observed adjudicated performance for one routing bucket."""

    total: int = 0
    correct: int = 0
    false_approves: int = 0

    @property
    def accuracy(self) -> float:
        return _rate(self.correct, self.total)

    @property
    def false_approve_rate(self) -> float:
        return _rate(self.false_approves, self.total)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "false_approves": self.false_approves,
            "false_approve_rate": self.false_approve_rate,
        }


@dataclass(frozen=True)
class ConfidenceCalibration:
    """Confidence caps learned from adjudicated review outcomes."""

    overall: CalibrationBucket
    by_recommendation: dict[str, CalibrationBucket] = field(default_factory=dict)
    by_slice: dict[str, CalibrationBucket] = field(default_factory=dict)
    min_examples: int = 5
    unsupported_bucket_cap: float = 0.9
    unsupported_safety_slice_cap: float = 0.85
    false_approve_cap: float = 0.5

    def confidence_cap(
        self,
        review: ReviewResult,
        slices: list[str] | tuple[str, ...] | None = None,
    ) -> float:
        """Return the maximum trusted confidence for this review context."""

        caps: list[float] = []
        predicted = review.recommendation.value
        if self.overall.total >= self.min_examples:
            caps.append(self.overall.accuracy)
        else:
            caps.append(self.unsupported_bucket_cap)

        rec_bucket = self.by_recommendation.get(predicted)
        caps.append(self._bucket_cap(rec_bucket, is_safety_slice=False))
        for slice_name in slices or []:
            is_safety = slice_name in SAFETY_CRITICAL_SLICE_NAMES
            caps.append(self._bucket_cap(self.by_slice.get(slice_name), is_safety_slice=is_safety))

        if (
            review.recommendation is Recommendation.APPROVE
            and any(bucket.false_approves > 0 for bucket in self._matching_buckets(predicted, slices))
        ):
            caps.append(self.false_approve_cap)

        return round(max(0.0, min(caps or [1.0])), 3)

    def as_dict(self) -> dict:
        return {
            "overall": self.overall.as_dict(),
            "by_recommendation": {
                key: bucket.as_dict()
                for key, bucket in sorted(self.by_recommendation.items())
            },
            "by_slice": {
                key: bucket.as_dict()
                for key, bucket in sorted(self.by_slice.items())
            },
            "min_examples": self.min_examples,
            "unsupported_bucket_cap": self.unsupported_bucket_cap,
            "unsupported_safety_slice_cap": self.unsupported_safety_slice_cap,
            "false_approve_cap": self.false_approve_cap,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConfidenceCalibration":
        return cls(
            overall=_bucket_from_dict(data.get("overall", {})),
            by_recommendation={
                key: _bucket_from_dict(value)
                for key, value in (data.get("by_recommendation") or {}).items()
            },
            by_slice={
                key: _bucket_from_dict(value)
                for key, value in (data.get("by_slice") or {}).items()
            },
            min_examples=int(data.get("min_examples", 5)),
            unsupported_bucket_cap=float(data.get("unsupported_bucket_cap", 0.9)),
            unsupported_safety_slice_cap=float(
                data.get("unsupported_safety_slice_cap", 0.85)
            ),
            false_approve_cap=float(data.get("false_approve_cap", 0.5)),
        )

    def _bucket_cap(
        self,
        bucket: CalibrationBucket | None,
        *,
        is_safety_slice: bool,
    ) -> float:
        if bucket is None or bucket.total < self.min_examples:
            return (
                self.unsupported_safety_slice_cap
                if is_safety_slice
                else self.unsupported_bucket_cap
            )
        cap = bucket.accuracy
        if bucket.false_approves:
            cap = min(cap, 1.0 - bucket.false_approve_rate)
        return cap

    def _matching_buckets(
        self,
        predicted: str,
        slices: list[str] | tuple[str, ...] | None,
    ) -> list[CalibrationBucket]:
        buckets = [self.overall]
        rec_bucket = self.by_recommendation.get(predicted)
        if rec_bucket is not None:
            buckets.append(rec_bucket)
        for slice_name in slices or []:
            slice_bucket = self.by_slice.get(slice_name)
            if slice_bucket is not None:
                buckets.append(slice_bucket)
        return buckets


@dataclass(frozen=True)
class AutoDecisionPolicy:
    """Thresholds that define when review output may count as auto-decided."""

    min_confidence: float = DEFAULT_AUTO_DECISION_THRESHOLD
    require_traceability: bool = True
    block_unknown_criteria: bool = True
    block_unresolved_conflicts: bool = True
    block_existing_human_review_gate: bool = True
    safety_critical_slices: tuple[str, ...] = SAFETY_CRITICAL_SLICE_NAMES
    calibration: ConfidenceCalibration | None = None


@dataclass
class ClinicalGoldCaseResult:
    """Scored outcome for one adjudicated clinical-review case."""

    case_id: str
    expected: str
    predicted: str | None
    auto_decided: bool
    correct: bool
    false_approve: bool
    confidence_score: float
    reasons: list[str] = field(default_factory=list)
    slices: list[str] = field(default_factory=list)
    criterion_total: int = 0
    criterion_correct: int = 0
    criterion_mismatches: list[dict] = field(default_factory=list)
    guideline_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "expected": self.expected,
            "predicted": self.predicted,
            "auto_decided": self.auto_decided,
            "correct": self.correct,
            "false_approve": self.false_approve,
            "confidence_score": self.confidence_score,
            "reasons": self.reasons,
            "slices": self.slices,
            "criterion_total": self.criterion_total,
            "criterion_correct": self.criterion_correct,
            "criterion_mismatches": self.criterion_mismatches,
            "guideline_id": self.guideline_id,
            "error": self.error,
        }


@dataclass
class ClinicalAccuracyReport:
    """Aggregate clinical-review scorecard for 99.9 auto-decision gating."""

    results: list[ClinicalGoldCaseResult] = field(default_factory=list)
    target_accuracy: float = 0.999
    calibration: ConfidenceCalibration | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def auto_decided_total(self) -> int:
        return sum(1 for result in self.results if result.auto_decided)

    @property
    def human_review_total(self) -> int:
        return self.total - self.auto_decided_total

    @property
    def coverage_rate(self) -> float:
        return _rate(self.auto_decided_total, self.total)

    @property
    def auto_decided_accuracy(self) -> float:
        decided = [result for result in self.results if result.auto_decided]
        return _rate(sum(1 for result in decided if result.correct), len(decided))

    @property
    def false_approve_count(self) -> int:
        return sum(1 for result in self.results if result.false_approve)

    @property
    def false_approve_rate(self) -> float:
        decided = [result for result in self.results if result.auto_decided]
        return _rate(sum(1 for result in decided if result.false_approve), len(decided))

    @property
    def traceability_rate(self) -> float:
        decided = [result for result in self.results if result.auto_decided]
        traceable = [
            result for result in decided
            if "lacks traceability" not in " ".join(result.reasons).lower()
        ]
        return _rate(len(traceable), len(decided))

    @property
    def safety_slice_failures(self) -> dict[str, int]:
        failures: dict[str, int] = {}
        for result in self.results:
            if not result.auto_decided or result.correct:
                continue
            for slice_name in result.slices:
                if slice_name in SAFETY_CRITICAL_SLICE_NAMES:
                    failures[slice_name] = failures.get(slice_name, 0) + 1
        return failures

    @property
    def criterion_total(self) -> int:
        return sum(result.criterion_total for result in self.results)

    @property
    def criterion_correct(self) -> int:
        return sum(result.criterion_correct for result in self.results)

    @property
    def criterion_accuracy(self) -> float:
        return _rate(self.criterion_correct, self.criterion_total)

    @property
    def passes_release_gate(self) -> bool:
        return (
            self.auto_decided_total > 0
            and self.auto_decided_accuracy >= self.target_accuracy
            and self.false_approve_count == 0
            and self.traceability_rate == 1.0
            and not self.safety_slice_failures
        )

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "auto_decided_total": self.auto_decided_total,
            "human_review_total": self.human_review_total,
            "coverage_rate": self.coverage_rate,
            "auto_decided_accuracy": self.auto_decided_accuracy,
            "false_approve_count": self.false_approve_count,
            "false_approve_rate": self.false_approve_rate,
            "traceability_rate": self.traceability_rate,
            "safety_slice_failures": self.safety_slice_failures,
            "criterion_total": self.criterion_total,
            "criterion_correct": self.criterion_correct,
            "criterion_accuracy": self.criterion_accuracy,
            "target_accuracy": self.target_accuracy,
            "passes_release_gate": self.passes_release_gate,
            "calibration": self.calibration.as_dict() if self.calibration else None,
            "results": [result.as_dict() for result in self.results],
        }


def evaluate_clinical_gold_set(
    scenarios: list[dict],
    reviewer=None,
    *,
    policy: AutoDecisionPolicy | None = None,
    target_accuracy: float = 0.999,
) -> ClinicalAccuracyReport:
    """Evaluate adjudicated scenarios with auto-decision safety metrics."""

    reviewer = reviewer or ClinicalReviewEngine()
    policy = policy or AutoDecisionPolicy()
    report = ClinicalAccuracyReport(
        target_accuracy=target_accuracy,
        calibration=policy.calibration,
    )
    for scenario in scenarios:
        report.results.append(
            evaluate_clinical_gold_case(scenario, reviewer, policy=policy)
        )
    return report


def evaluate_clinical_gold_case(
    scenario: dict,
    reviewer,
    *,
    policy: AutoDecisionPolicy,
) -> ClinicalGoldCaseResult:
    case_id = str(scenario.get("case_id") or scenario.get("name") or "?")
    expected = str(scenario["expected"]).upper()
    slices = [str(item).lower() for item in scenario.get("slices", [])]
    try:
        raw = reviewer.review(scenario["case"], scenario.get("document_text"))
        review, guideline_id = _coerce_to_review_result(raw)
        if review is None:
            return ClinicalGoldCaseResult(
                case_id=case_id,
                expected=expected,
                predicted=None,
                auto_decided=False,
                correct=False,
                false_approve=False,
                confidence_score=0.0,
                reasons=["Reviewer did not return a ReviewResult."],
                slices=slices,
            )
        reasons = auto_decision_reasons(review, policy, slices=slices)
        auto_decided = not reasons
        predicted = review.recommendation.value
        correct = predicted == expected
        criterion_total, criterion_correct, criterion_mismatches = (
            compare_criterion_labels(review, scenario.get("adjudication", {}))
        )
        return ClinicalGoldCaseResult(
            case_id=case_id,
            expected=expected,
            predicted=predicted,
            auto_decided=auto_decided,
            correct=correct,
            false_approve=(
                auto_decided
                and predicted == Recommendation.APPROVE.value
                and expected != Recommendation.APPROVE.value
            ),
            confidence_score=calibrated_confidence(review, policy, slices=slices),
            reasons=reasons,
            slices=slices,
            criterion_total=criterion_total,
            criterion_correct=criterion_correct,
            criterion_mismatches=criterion_mismatches,
            guideline_id=guideline_id or review.guideline_id,
        )
    except Exception as exc:  # noqa: BLE001 - record validation failure
        return ClinicalGoldCaseResult(
            case_id=case_id,
            expected=expected,
            predicted=None,
            auto_decided=False,
            correct=False,
            false_approve=False,
            confidence_score=0.0,
            reasons=["Reviewer raised an exception."],
            slices=slices,
            error=f"{type(exc).__name__}: {exc}",
        )


def auto_decision_reasons(
    review: ReviewResult,
    policy: AutoDecisionPolicy | None = None,
    *,
    slices: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Return reasons a review must abstain to human review."""

    policy = policy or AutoDecisionPolicy()
    reasons: list[str] = []
    confidence = calibrated_confidence(review, policy, slices=slices)
    if confidence < policy.min_confidence:
        reasons.append(
            f"Calibrated review confidence {confidence:.2f} below auto-decision "
            f"threshold {policy.min_confidence:.2f}."
        )

    gate = review.safety_gate or {}
    gate_status = str(gate.get("status") or "")
    if (
        policy.block_existing_human_review_gate
        and gate_status == SafetyGateStatus.HUMAN_REVIEW_REQUIRED.value
    ):
        reasons.append("Review safety gate already requires human review.")
    if policy.block_unresolved_conflicts and gate.get("unresolved_conflicts"):
        reasons.append("Review has unresolved conflicts.")
    if gate.get("requires_human_review_reason"):
        reasons.append(str(gate["requires_human_review_reason"]))

    if policy.block_unknown_criteria:
        unknown = [
            detail.id
            for detail in review.criteria_detail
            if detail.status is CriterionStatus.UNKNOWN
        ]
        if unknown:
            reasons.append("Criterion status unknown: " + ", ".join(unknown) + ".")

    if policy.require_traceability:
        missing_traceability = [
            detail.id
            for detail in review.criteria_detail
            if not _criterion_has_traceability(detail)
        ]
        if missing_traceability:
            reasons.append(
                "Criterion lacks traceability: "
                + ", ".join(missing_traceability)
                + "."
            )

    return list(dict.fromkeys(reasons))


def calibrated_confidence(
    review: ReviewResult,
    policy: AutoDecisionPolicy | None = None,
    *,
    slices: list[str] | tuple[str, ...] | None = None,
) -> float:
    """Return a conservative confidence score adjusted for review quality."""

    policy = policy or AutoDecisionPolicy()
    quality_penalty = 0.0
    if review.safety_gate.get("unresolved_conflicts"):
        quality_penalty += 0.12
    if review.safety_gate.get("status") == SafetyGateStatus.HUMAN_REVIEW_REQUIRED.value:
        quality_penalty += 0.1
    if policy.require_traceability:
        missing_traceability = [
            detail.id
            for detail in review.criteria_detail
            if not _criterion_has_traceability(detail)
        ]
        quality_penalty += min(0.1, 0.02 * len(missing_traceability))
    if any(detail.status is CriterionStatus.UNKNOWN for detail in review.criteria_detail):
        quality_penalty += 0.05
    if policy.calibration is not None:
        confidence = policy.calibration.confidence_cap(review, slices) - quality_penalty
        if review.confidence_score < 0.5:
            confidence = min(confidence, review.confidence_score)
    else:
        confidence = review.confidence_score - quality_penalty
    return round(max(0.0, min(1.0, confidence)), 3)


def build_confidence_calibration(
    scenarios: list[dict],
    reviewer=None,
    *,
    base_policy: AutoDecisionPolicy | None = None,
    min_examples: int = 5,
    unsupported_bucket_cap: float = 0.9,
    unsupported_safety_slice_cap: float = 0.85,
    false_approve_cap: float = 0.5,
) -> ConfidenceCalibration:
    """Learn confidence caps from adjudicated case outcomes."""

    reviewer = reviewer or ClinicalReviewEngine()
    policy = base_policy or AutoDecisionPolicy(
        min_confidence=0.0,
        require_traceability=False,
        block_unknown_criteria=False,
        block_unresolved_conflicts=False,
        block_existing_human_review_gate=False,
    )
    overall = _BucketCounter()
    by_recommendation: dict[str, _BucketCounter] = {}
    by_slice: dict[str, _BucketCounter] = {}

    for scenario in scenarios:
        expected = str(scenario["expected"]).upper()
        slices = [str(item).lower() for item in scenario.get("slices", [])]
        try:
            raw = reviewer.review(scenario["case"], scenario.get("document_text"))
            review, _ = _coerce_to_review_result(raw)
            if review is None:
                continue
        except Exception:  # noqa: BLE001 - failed reviewer output is not a confidence example
            continue

        predicted = review.recommendation.value
        correct = predicted == expected
        false_approve = (
            predicted == Recommendation.APPROVE.value
            and expected != Recommendation.APPROVE.value
        )
        overall.add(correct=correct, false_approve=false_approve)
        by_recommendation.setdefault(predicted, _BucketCounter()).add(
            correct=correct,
            false_approve=false_approve,
        )
        for slice_name in slices:
            by_slice.setdefault(slice_name, _BucketCounter()).add(
                correct=correct,
                false_approve=false_approve,
            )

    return ConfidenceCalibration(
        overall=overall.freeze(),
        by_recommendation={
            key: value.freeze() for key, value in by_recommendation.items()
        },
        by_slice={key: value.freeze() for key, value in by_slice.items()},
        min_examples=min_examples,
        unsupported_bucket_cap=unsupported_bucket_cap,
        unsupported_safety_slice_cap=unsupported_safety_slice_cap,
        false_approve_cap=false_approve_cap,
    )


def compare_criterion_labels(
    review: ReviewResult,
    adjudication: dict,
) -> tuple[int, int, list[dict]]:
    """Compare review criterion status against human-adjudicated labels."""

    labels = {
        _norm_id(item.get("criterion_id")): str(item.get("status") or "").upper()
        for item in adjudication.get("criteria", [])
        if item.get("criterion_id")
    }
    if not labels:
        return 0, 0, []

    details = {
        _norm_id(detail.id): detail.status.value if detail.status else None
        for detail in review.criteria_detail
    }
    total = len(labels)
    correct = 0
    mismatches: list[dict] = []
    for criterion_id, expected in labels.items():
        predicted = _norm_status(details.get(criterion_id))
        expected_status = _norm_status(expected)
        if predicted == expected_status:
            correct += 1
        else:
            mismatches.append(
                {
                    "criterion_id": criterion_id,
                    "expected": expected_status,
                    "predicted": predicted,
                }
            )
    return total, correct, mismatches


def _criterion_has_traceability(detail) -> bool:
    if detail.status is CriterionStatus.UNKNOWN:
        return bool(detail.missing_evidence)
    if detail.status is CriterionStatus.MET:
        return bool(detail.supporting_evidence_ids)
    if detail.status is CriterionStatus.NOT_MET:
        return bool(detail.not_met_evidence_ids or detail.missing_evidence)
    return False


@dataclass
class _BucketCounter:
    total: int = 0
    correct: int = 0
    false_approves: int = 0

    def add(self, *, correct: bool, false_approve: bool) -> None:
        self.total += 1
        if correct:
            self.correct += 1
        if false_approve:
            self.false_approves += 1

    def freeze(self) -> CalibrationBucket:
        return CalibrationBucket(
            total=self.total,
            correct=self.correct,
            false_approves=self.false_approves,
        )


def _bucket_from_dict(data: dict) -> CalibrationBucket:
    return CalibrationBucket(
        total=int(data.get("total", 0)),
        correct=int(data.get("correct", 0)),
        false_approves=int(data.get("false_approves", 0)),
    )


def _norm_id(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _norm_status(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"met", "satisfied", "true"}:
        return CriterionStatus.MET.value
    if text in {"not_met", "unmet", "missing", "failed", "false"}:
        return CriterionStatus.NOT_MET.value
    return CriterionStatus.UNKNOWN.value


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0

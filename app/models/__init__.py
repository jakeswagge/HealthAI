"""Data models for HealthAI."""

from app.models.document import (
    DocumentType,
    ExtractedDocument,
    SUPPORTED_EXTENSIONS,
)
from app.models.patient_case import (
    CORE_FIELDS,
    Decision,
    FieldSource,
    PatientCase,
)
from app.models.clinical_guideline import (
    ClinicalGuideline,
    Contraindication,
    GuidelineCriterion,
)
from app.models.review_result import (
    CriterionEvaluation,
    Recommendation,
    ReviewResult,
)
from app.models.appeal_letter import AppealLetter
from app.models.case_record import (
    CaseRecord,
    CaseStatus,
    HumanDecision,
    HumanReviewDecision,
)
from app.models.audit_event import (
    AuditActor,
    AuditEvent,
    AuditEventType,
)
from app.models.case_document import (
    CaseDocument,
    DocumentCategory,
    classify_document,
    new_document_id,
    PAGE_DELIMITER,
)
from app.models.evidence_reference import EvidenceReference, new_evidence_id
from app.models.clinical_fact import (
    AssertionStatus,
    ClinicalFact,
    ClinicalFactDomain,
    ConflictStatus,
    DiagnosisState,
    GovernanceStatus,
    ProviderState,
    StepTherapyState,
    TBScreenState,
    TemporalityStatus,
    new_clinical_fact_id,
)
from app.models.conflict_report import (
    ConflictReport,
    ConflictSeverity,
    FactConflict,
)
from app.models.unified_case_context import ResolvedFact, UnifiedCaseContext
from app.models.conflict_resolution import (
    AuthoritativeFact,
    ConflictResolution,
    ResolutionSource,
    new_fact_id,
    new_resolution_id,
)
from app.models.reviewer_feedback import (
    FeedbackTarget,
    FeedbackVerdict,
    ReviewerFeedback,
    new_feedback_id,
)
from app.models.ocr_result import (
    DEFAULT_OCR_CONFIDENCE_THRESHOLD,
    OCRPageResult,
    ProcessingMethod,
    new_ocr_id,
)
from app.models.evidence_quality import (
    EvidenceQualityAssessment,
    WEAK_EVIDENCE_THRESHOLD,
    new_assessment_id,
)
from app.models.evidence_review_decision import (
    EvidenceDecision,
    EvidenceReviewDecision,
    new_decision_id,
)
from app.models.governance import (
    ApprovedEvidenceSet,
    ComplianceViolation,
    EvidenceMode,
    ExcludedEvidence,
    GovernanceComplianceReport,
    GovernanceSettings,
)
from app.models.explanation import (
    AppealExplanation,
    EvidenceLineage,
    ReviewExplanation,
    TraceabilityChain,
    new_appeal_explanation_id,
    new_review_explanation_id,
)
from app.models.payer import KNOWN_PACKS, PayerProfile, PayerStatus
from app.models.operational_health import OperationalHealthReport
from app.models.patient_case import NormalizedField
from app.models.safety import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    AppealVerificationResult,
    AppealVerificationStatus,
    SafetyGateDecision,
    SafetyGateStatus,
)

__all__ = [
    "DocumentType",
    "ExtractedDocument",
    "SUPPORTED_EXTENSIONS",
    "CORE_FIELDS",
    "Decision",
    "FieldSource",
    "NormalizedField",
    "PatientCase",
    "ClinicalGuideline",
    "Contraindication",
    "GuidelineCriterion",
    "CriterionEvaluation",
    "Recommendation",
    "ReviewResult",
    "AppealLetter",
    "CaseRecord",
    "CaseStatus",
    "HumanDecision",
    "HumanReviewDecision",
    "AuditActor",
    "AuditEvent",
    "AuditEventType",
    "CaseDocument",
    "DocumentCategory",
    "classify_document",
    "new_document_id",
    "PAGE_DELIMITER",
    "EvidenceReference",
    "new_evidence_id",
    "AssertionStatus",
    "ClinicalFact",
    "ClinicalFactDomain",
    "ConflictStatus",
    "DiagnosisState",
    "GovernanceStatus",
    "ProviderState",
    "StepTherapyState",
    "TBScreenState",
    "TemporalityStatus",
    "new_clinical_fact_id",
    "ConflictReport",
    "ConflictSeverity",
    "FactConflict",
    "ResolvedFact",
    "UnifiedCaseContext",
    "AuthoritativeFact",
    "ConflictResolution",
    "ResolutionSource",
    "new_fact_id",
    "new_resolution_id",
    "FeedbackTarget",
    "FeedbackVerdict",
    "ReviewerFeedback",
    "new_feedback_id",
    "DEFAULT_OCR_CONFIDENCE_THRESHOLD",
    "OCRPageResult",
    "ProcessingMethod",
    "new_ocr_id",
    "EvidenceQualityAssessment",
    "WEAK_EVIDENCE_THRESHOLD",
    "new_assessment_id",
    "EvidenceDecision",
    "EvidenceReviewDecision",
    "new_decision_id",
    "ApprovedEvidenceSet",
    "ComplianceViolation",
    "EvidenceMode",
    "ExcludedEvidence",
    "GovernanceComplianceReport",
    "GovernanceSettings",
    "AppealExplanation",
    "EvidenceLineage",
    "ReviewExplanation",
    "TraceabilityChain",
    "new_appeal_explanation_id",
    "new_review_explanation_id",
    "KNOWN_PACKS",
    "PayerProfile",
    "PayerStatus",
    "OperationalHealthReport",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "AppealVerificationResult",
    "AppealVerificationStatus",
    "SafetyGateDecision",
    "SafetyGateStatus",
]

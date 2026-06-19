"""Evidence verification for generated appeal letters."""

from __future__ import annotations

from app.evidence.linker import link_appeal
from app.models.appeal_letter import AppealLetter
from app.models.safety import AppealVerificationResult, AppealVerificationStatus
from app.models.unified_case_context import UnifiedCaseContext
from app.services.llm_client import LLMClient


SAFE_UNSUPPORTED_TEXT = (
    "Documentation was not available in the reviewed record; no unsupported "
    "clinical claim is asserted."
)


class AppealVerifier:
    """Verify appeal claims against source-backed evidence."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client

    def verify(
        self,
        appeal: AppealLetter,
        context: UnifiedCaseContext,
    ) -> AppealLetter:
        """Attach verification metadata, correcting unsupported sections."""
        linked, unsupported = link_appeal(appeal, context)
        unsupported = _append_unique(
            unsupported,
            _unsupported_claim_sections(linked, context),
        )
        cited_ids = sorted(
            {
                ev_id
                for ids in (linked.section_evidence or {}).values()
                for ev_id in ids
            }
        )

        if not unsupported:
            linked.verification = AppealVerificationResult(
                status=AppealVerificationStatus.PASSED,
                unsupported_claims=[],
                verifier_backend=getattr(self.llm, "name", "local-verifier"),
                verifier_model=getattr(self.llm, "model", "local-verifier"),
                cited_evidence_ids=cited_ids,
            )
            linked.evidence_ids = cited_ids
            return linked

        corrected = linked.model_copy(deep=True)
        for section in unsupported:
            if section == "clinical_summary":
                corrected.clinical_summary = SAFE_UNSUPPORTED_TEXT
            elif section == "appeal_reason":
                corrected.appeal_reason = SAFE_UNSUPPORTED_TEXT

        corrected.letter_text = _rewrite_letter_text(
            corrected.letter_text,
            unsupported,
        )
        corrected.verification = AppealVerificationResult(
            status=AppealVerificationStatus.CORRECTED,
            unsupported_claims=unsupported,
            corrected_text=corrected.letter_text,
            verifier_backend=getattr(self.llm, "name", "local-verifier"),
            verifier_model=getattr(self.llm, "model", "local-verifier"),
            cited_evidence_ids=cited_ids,
        )
        corrected.evidence_ids = cited_ids
        return corrected


def _rewrite_letter_text(letter_text: str, unsupported: list[str]) -> str:
    """Append a correction notice for unsupported appeal sections."""
    if not unsupported:
        return letter_text
    sections = ", ".join(unsupported)
    notice = (
        "\n\n## Evidence Verification\n"
        f"The following section(s) lacked direct source support and were "
        f"treated as documentation gaps: {sections}. "
        f"{SAFE_UNSUPPORTED_TEXT}"
    )
    if "## Evidence Verification" in letter_text:
        return letter_text
    return letter_text.rstrip() + notice


def _unsupported_claim_sections(
    appeal: AppealLetter,
    context: UnifiedCaseContext,
) -> list[str]:
    by_id = {ev.evidence_id: ev for ev in context.evidence}
    unsupported: list[str] = []
    for section, text in (
        ("clinical_summary", appeal.clinical_summary),
        ("appeal_reason", appeal.appeal_reason),
    ):
        required = _required_fact_types_for_claim(text)
        if not required:
            continue
        cited = [
            by_id[ev_id]
            for ev_id in (appeal.section_evidence or {}).get(section, [])
            if ev_id in by_id
        ]
        cited_types = {ev.fact_type for ev in cited}
        missing_groups = [
            group for group in required
            if cited_types.isdisjoint(group)
        ]
        if missing_groups:
            unsupported.append(section)
    return unsupported


def _required_fact_types_for_claim(text: str) -> list[set[str]]:
    low = (text or "").lower()
    required: list[set[str]] = []
    if any(token in low for token in ("methotrexate", "mtx", "dmard")):
        required.append({"step_therapy_status", "criterion_step_therapy"})
    if any(token in low for token in ("tb", "tuberculosis", "quantiferon", "ppd")):
        required.append({"tb_screen_result", "criterion_tb_screen"})
    if "specialist" in low or "rheumatolog" in low:
        required.append({"criterion_specialist", "specialist_status", "provider_role"})
    if "diagnosis" in low or "rheumatoid arthritis" in low:
        required.append({"diagnosis", "diagnosis_assertion", "icd10_codes"})
    return required


def _append_unique(existing: list[str], additions: list[str]) -> list[str]:
    out = list(existing)
    for item in additions:
        if item not in out:
            out.append(item)
    return out

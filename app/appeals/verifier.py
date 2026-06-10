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

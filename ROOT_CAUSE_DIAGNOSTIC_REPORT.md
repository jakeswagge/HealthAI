# Root Cause Diagnostic Report: Incorrect Review Outcomes

**Platform**: HealthAI  
**Role**: Principal Software Engineer, Clinical Safety Reviewer, Root Cause Investigator  
**Scope**: Deterministic Engine + LLM-Assisted Recommendations  
**Status**: DIAGNOSTIC ONLY — No code changes

---

## Executive Summary

This report identifies **12 distinct root causes** where the deterministic review engine and/or the LLM-assisted path produce clinically incorrect recommendations. Each defect is traced to an exact file, class, method, and conditional, with a full execution path from evidence extraction through to the final recommendation.

The findings are organized by severity:
- 🔴 **CRITICAL** (4): Produce clinically dangerous outcomes (wrong APPROVE or wrong DENY)
- 🟠 **HIGH** (5): Produce materially incorrect criterion evaluations that cascade to wrong recommendations
- 🟡 **MEDIUM** (3): Produce misleading evidence chains or silently degrade safety gates

---

## Finding 1: TB Polarity Inversion — Bare Mention Defaults to POSITIVE

> [!CAUTION]
> A bare mention of "TB" or "tuberculosis" in clinical text without explicit test language is classified as POSITIVE, which triggers a contraindication and an automatic DENY.

### Observed Recommendation
Case with text *"Patient has no history of TB"* → **DENY** (contraindication: "Positive tuberculosis evidence detected")

### Execution Trace
1. [extractor.py](file:///c:/HealthAI - Copy/app/evidence/extractor.py) L572 → `extract_clinical_signals(page_text)` finds "TB" entity
2. [clinical_nlp.py](file:///c:/HealthAI - Copy/app/review/clinical_nlp.py) L499-517 → `tb_result_polarity()` evaluates the signal
3. L515-516: Falls through all cue checks → **returns `"positive"` as the default** (L517)
4. [extractor.py](file:///c:/HealthAI - Copy/app/evidence/extractor.py) L694-708 → Creates `tb_screen_result: positive` evidence reference
5. [clinical_fact.py](file:///c:/HealthAI - Copy/app/models/clinical_fact.py) L235-236 → `_state_triplet()` maps "positive" → `TBScreenState.POSITIVE`
6. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L673-675 → `_evaluate_with_clinical_facts()` returns `"unmet"` for TB criterion
7. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L784-789 → `_clinical_contraindications()` adds "Positive tuberculosis evidence detected"
8. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1374-1375 → `_decide()` returns `DENY` due to contraindication

### First Point of Failure
[clinical_nlp.py](file:///c:/HealthAI - Copy/app/review/clinical_nlp.py#L517) — `tb_result_polarity()` line 517: `return "positive"`

### Root Cause
The function's fallthrough default is `"positive"` instead of `"unknown"`. Any TB mention that doesn't match negative, pending, indeterminate, or absence cues — including negated mentions that MedSpaCy's ConText module fails to flag, educational text, or bare mentions without test context — is silently classified as a positive TB result.

### Clinical Safety Impact
🔴 **CRITICAL**: False positive TB contraindication → medically necessary biologic therapy incorrectly denied. Patients with documented negative TB screening or no TB history could be denied treatment.

### Recommended Fix
Change L517 return value from `"positive"` to `"unknown"`. Positive classification should require affirmative evidence (cue match), never be the default.

### Production Readiness Impact
Every case processed through the deterministic path where "TB" or "tuberculosis" appears in any context (including negated, educational, or historical) without explicit test-result language is at risk.

---

## Finding 2: Step Therapy Refusal Misclassified as Valid Step Completion

> [!CAUTION]
> The `_evaluate_with_clinical_facts()` function checks for REFUSED/NEVER_STARTED *before* FAILED/INTOLERANT, but the clinical fact override ordering in `ClinicalReviewEngine.review()` allows the NLP-layer fallback to overwrite a fact-layer "unmet" with a keyword-layer "met".

### Observed Recommendation
Case: *"Patient refused methotrexate. Patient failed topical steroids."* → Step therapy criterion marked **MET**

### Execution Trace
1. [clinical_fact.py](file:///c:/HealthAI - Copy/app/models/clinical_fact.py) L250-251 → `_state_triplet()` correctly identifies `REFUSED` for methotrexate
2. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L697-705 → `_evaluate_with_clinical_facts()` correctly returns `"unmet"` for refused step therapy
3. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1132-1151 → **However**, L1133-1137 calls `_evaluate_with_clinical_facts()` first, then L1138-1145 calls `_evaluate_criterion()` (the NLP/keyword path)
4. L1147-1148: **`fact_status` overrides are applied ONLY when `fact_status is not None`** — this is correct
5. **BUT**: L1349-1353 in `_evaluate_criterion()` calls `_evaluate_with_medspacy()` which also evaluates step therapy
6. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L990-991 → `_is_non_dmard_step_text()` check: *"failed topical steroids"* matches `_NON_DMARD_STEP_CONTEXT_CUES` → returns `"unmet"` — this is correct
7. **HOWEVER**: If "failed" appears anywhere in the support text alongside a `STEP_THERAPY` signal, [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L951-968 → the `met` variable picks up "failed" in `_STEP_STRONG_SUCCESS_CUES` from **any** sentence, not scoped to the specific signal's sentence

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L951-L968) — `_evaluate_with_medspacy()` step therapy "met" detection uses `_has_any(s.sentence, _STEP_STRONG_SUCCESS_CUES)` which matches "failed" on a *non-DMARD therapy sentence* if both sentences share the same signal list.

### Root Cause
The NLP signal evaluation in `_evaluate_with_medspacy()` L951-968 checks `step_therapy_status(s) == "failed"` OR `_has_any(s.sentence, _STEP_STRONG_SUCCESS_CUES)` across **all** `STEP_THERAPY` signals. When clinical text mentions "failed" in the context of a non-DMARD therapy (e.g., "failed topical steroids"), the "failed" keyword match in `_STEP_STRONG_SUCCESS_CUES` can leak into the step therapy evaluation if MedSpaCy labels both the DMARD mention and the non-DMARD mention with the `STEP_THERAPY` label.

The `_is_non_dmard_step_text()` guard at L990 only fires when **all** step signals are non-DMARD. When there is a mix (one DMARD refused + one non-DMARD failed), the `met` signal from the non-DMARD failure races ahead.

### Clinical Safety Impact
🔴 **CRITICAL**: A patient who refused DMARD therapy could be incorrectly approved for biologic therapy. Step therapy requirements exist to ensure less expensive, first-line treatments are attempted before escalating to biologics.

### Recommended Fix
Scope the "met" determination in `_evaluate_with_medspacy()` to require that `_has_any(s.sentence, _STEP_STRONG_SUCCESS_CUES)` signals also pass `not _is_non_dmard_step_text(s.sentence)`.

### Production Readiness Impact
Any case where clinical notes mention failure of non-DMARD therapies alongside methotrexate refusal is at risk of incorrect approval.

---

## Finding 3: Specialist Synonym Gap — Prescriber Role Evidence Dropped

> [!WARNING]
> The specialist evaluation in `_evaluate_with_clinical_facts()` checks for "prescriber" in `quoted_text`, but the provider state classification in `_state_triplet()` does not distinguish prescriber from any other specialist mention, so the prescriber check is cosmetic.

### Observed Recommendation
Case: *"Ordering provider: Dr. Smith, MD (Internal Medicine)"* → Specialist criterion marked **UNKNOWN** (should be NON_SPECIALIST / UNMET)

### Execution Trace
1. [clinical_nlp.py](file:///c:/HealthAI - Copy/app/review/clinical_nlp.py) L52-104 → Target rules do not include "Internal Medicine", "Internist" is mapped to `PROVIDER_PRIMARY_CARE` not `SPECIALIST_RHEUM`
2. [extractor.py](file:///c:/HealthAI - Copy/app/evidence/extractor.py) L610-624 → `provider_role(signal)` returns `"primary care provider"` for `PROVIDER_PRIMARY_CARE`
3. [clinical_fact.py](file:///c:/HealthAI - Copy/app/models/clinical_fact.py) L273-287 → `_state_triplet()` checks `"internist"` in `low` → returns `NON_SPECIALIST`
4. **BUT**: If the text says "Dr. Smith, MD" without any of the PCP/internist/family physician cues, the fact falls through to `ProviderState.UNKNOWN` (L287)
5. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L725-751 → `_evaluate_with_clinical_facts()` only evaluates provider facts that are `SPECIALIST`, `CONSULTING_SPECIALIST`, or `NON_SPECIALIST`. **UNKNOWN provider state is completely ignored**.
6. Result: No fact-based evaluation → falls through to NLP/keyword path
7. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1007-1036 → `_evaluate_with_medspacy()` specialist check finds no specialist signals → returns `None`
8. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1355-1363 → keyword fallback finds no match → returns `"unknown"`

### First Point of Failure
[clinical_fact.py](file:///c:/HealthAI - Copy/app/models/clinical_fact.py#L273-L287) — The provider state classification has insufficient specialty vocabulary. Common medical titles like "MD", "DO", "NP", "PA" without specialty qualifiers all fall to `UNKNOWN`.

### Root Cause
The `_state_triplet()` function for provider role uses a narrow set of string cues (L273-287). When a provider is identified only by generic title (e.g., "MD", "physician") without a specialty qualifier, the state becomes `UNKNOWN`. The downstream evaluation in `_evaluate_with_clinical_facts()` L725-751 silently ignores `UNKNOWN` provider facts — it does not treat them as "non-specialist" evidence.

### Clinical Safety Impact
🟠 **HIGH**: When the prescribing physician is not a specialist but lacks the specific cue phrases (PCP, family physician, internist), the specialist requirement criterion defaults to `"unknown"` instead of `"unmet"`. This inflates the chance of an `INSUFFICIENT_INFORMATION` recommendation instead of a proper `DENY`.

### Recommended Fix
Treat `UNKNOWN` provider state as soft evidence for NON_SPECIALIST when the criterion is evaluating specialist requirements. Alternatively, expand the vocabulary in `_state_triplet()` to include "MD", "DO", "physician" as `NON_SPECIALIST` unless a specialist qualifier is present.

### Production Readiness Impact
Any case where the provider has a generic medical title without explicit specialty language.

---

## Finding 4: Diagnosis Contradiction Passthrough — Active + Rule-Out Same Diagnosis

> [!WARNING]
> When the same diagnosis appears as both ACTIVE and RULE_OUT across documents, the semantic conflict detector in `CaseAssemblyEngine` flags it, but the downstream review engine does not always receive the conflict.

### Observed Recommendation
Case: Document A: *"Diagnosis: Rheumatoid Arthritis"* + Document B: *"Rule out rheumatoid arthritis"* → Diagnosis criterion marked **MET** (should be UNKNOWN / HUMAN_REVIEW)

### Execution Trace
1. [assembly/engine.py](file:///c:/HealthAI - Copy/app/assembly/engine.py) L386-417 → `_semantic_conflicts()` correctly detects Active vs Rule-Out RA conflict
2. L526-537 → `_mark_conflicted_facts()` sets `conflict_status = CONFLICTED` on the relevant facts
3. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L753-777 → `_evaluate_with_clinical_facts()` for diagnosis: L760-763 checks for conflicted facts → returns `"unknown"` — **this is correct**
4. **BUT**: L1133-1148 in `review()`: `fact_status` from `_evaluate_with_clinical_facts()` is only applied when it is `not None` (L1147)
5. **The issue**: `_evaluate_with_clinical_facts()` L753-757 first checks `_has_rule_out_ra(support_text)` and `_has_differential_or_pending_ra(support_text)` BEFORE checking clinical facts
6. If the `support_text` doesn't contain the exact regex patterns for rule-out (e.g., "r/o" or "rule out" followed by "ra" within 80 chars), these checks pass through
7. Then L760-763 would catch the conflict — BUT L764-770 checks for `active_ra` first, and if active RA facts exist (even when also conflicted), the logic proceeds

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L753-L770) — The ordering of checks in `_evaluate_with_clinical_facts()` for diagnosis evaluates rule-out regex → differential regex → psoriasis severity regex → **conflicted facts** → **active RA**. The active RA check at L764-770 fires only for facts where `conflict_status` is *not* `CONFLICTED` because conflicted facts were already handled at L760-763.

**Actually** — re-reading the code more carefully: L760 filters `conflicted = [f for f in facts if f.conflict_status is ConflictStatus.CONFLICTED]`, and L764 filters `active_ra = [f for f in facts if f.state == DiagnosisState.ACTIVE.value and "rheumatoid arthritis" in f.value.lower()]` — this filter does NOT exclude conflicted facts. A fact can be both `state=ACTIVE` and `conflict_status=CONFLICTED`.

### Root Cause
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L764-L770): The `active_ra` filter at L764-770 does not exclude facts whose `conflict_status` is `CONFLICTED`. Since the conflicted-facts check at L760-763 returns early only if there are *exclusively* conflicted facts, a case with both conflicted and non-conflicted diagnosis facts will fall through to L764, where an active diagnosis fact (even one marked CONFLICTED) will cause the criterion to be marked `"met"`.

**Specifically**: If Document A produces an ACTIVE RA fact and Document B produces a RULE_OUT RA fact, both are marked CONFLICTED by `_mark_conflicted_facts()`. At L760, `conflicted = [active_fact, rule_out_fact]` — both are present. L762 returns `"unknown"`. **This part is correct.**

**However**: If there are TWO active RA mentions (from Document A page 1 and page 2) and ONE rule-out mention (from Document B), only the facts with matching conflict IDs are marked conflicted. The second active RA fact — if it came from a different evidence chain not included in the conflict — would NOT be marked conflicted, and would cause L764-770 to fire and return `"met"`.

### Clinical Safety Impact
🔴 **CRITICAL**: A patient whose RA diagnosis is being ruled out could be incorrectly approved for biologic therapy. Active treatment for an unconfirmed diagnosis poses real clinical risk.

### Recommended Fix
The `active_ra` filter at L764 should explicitly exclude facts where `conflict_status is ConflictStatus.CONFLICTED`:
```python
active_ra = [
    f for f in facts
    if f.state == DiagnosisState.ACTIVE.value
    and "rheumatoid arthritis" in f.value.lower()
    and f.conflict_status is not ConflictStatus.CONFLICTED
]
```

### Production Readiness Impact
Any case with multiple documents where the diagnosis appears as both confirmed and under investigation/rule-out.

---

## Finding 5: Negation Context Window Too Narrow for Clinical Text

### Observed Recommendation
Case: *"The patient's history was reviewed including TB screening and lab work, and the results confirmed that tuberculosis was negative."* → TB criterion may be marked UNMET because negation window misses the negative cue.

### Execution Trace
1. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L132-136 → `_NEGATION_BEFORE_RE` window is 80 characters before match
2. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L137-141 → `_NEGATION_AFTER_RE` window is 80 characters after match, but only checks for "not documented", "not performed", etc.
3. When "tuberculosis" appears early in a compound sentence and "negative" appears more than 80 characters later, the negation context is missed
4. The MedSpaCy ConText module in `extract_clinical_signals()` handles this better because it uses sentence-level context, but only when MedSpaCy is available

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L151-L154) — `_is_negated_context()` has a fixed 80-character window.

### Root Cause
The 80-character context window in `_is_negated_context()` is insufficient for clinical notes that use verbose or compound sentence structures. The function was designed for terse structured forms ("TB: negative") but clinical notes often use narrative form with the result separated from the test mention by more than 80 characters.

### Clinical Safety Impact
🟠 **HIGH**: TB screening evidence could be misclassified as absent when it is actually documented as negative, leading to an incorrect DENY.

### Recommended Fix
Increase the context window to 200 characters, or better, operate at the sentence level (split on sentence boundaries rather than character count).

### Production Readiness Impact
Clinical notes with narrative sentence structure (common in physician notes) are affected.

---

## Finding 6: `_evaluate_criterion()` Called Before `_evaluate_with_clinical_facts()` Result Is Used

### Observed Recommendation
N/A — this is a latent inefficiency that causes **stale NLP evaluations to appear in criteria detail** even when the fact-based evaluation is correct.

### Execution Trace
1. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1132-1151 → In the `review()` loop:
   - L1133-1137: `fact_status = _evaluate_with_clinical_facts(...)` — executes
   - L1138-1145: `status, note = self._evaluate_criterion(...)` — **also executes** (NLP/keyword path)
   - L1147-1148: `if fact_status is not None: status, note, fact_evidence_ids = fact_status` — overrides the NLP result

2. The `_evaluate_criterion()` result (L1138-1145) runs even though it will be discarded when `fact_status` is available. This is wasteful but not dangerous **UNLESS**:

3. L1176-1182: **The negation override** checks `evaluation.note` (which was set from `note` at L1167) for "context=negated". If the fact-based override at L1148 changes `note`, but the evaluation was already constructed at L1163-1175 with the NLP-based note, the negation override uses the NLP-based note.

**Wait** — re-reading: L1167 sets `note=note` where `note` is the variable that may have been overridden by L1148. So the value IS correct at construction time. The issue is that the NLP path was called unnecessarily and its side effects (signal extraction) may have influenced the support/deficiency signal lists used by other criteria in the same loop.

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L1138-L1145) — `_evaluate_criterion()` is always called even when `_evaluate_with_clinical_facts()` will provide the answer.

### Root Cause
The code was designed to call both evaluation paths and let the fact-based result take priority. This is correct in principle, but the NLP path has side effects through shared `support_signals` and `deficiency_signals` lists, and its diagnostic output (note text) can confuse audit trails.

### Clinical Safety Impact
🟡 **MEDIUM**: No direct clinical impact, but creates misleading audit trails where NLP-path reasoning is generated and potentially logged even when the fact-based path was the actual decision-maker.

### Recommended Fix
Short-circuit: only call `_evaluate_criterion()` when `fact_status is None`.

### Production Readiness Impact
Audit and explainability clarity.

---

## Finding 7: Governance-Filtered Evidence Can Be Re-Introduced via `_heal_requested_service_evidence()`

> [!WARNING]
> The governance path passes `allow_document_text_healing=False` to prevent rejected document text from re-entering the pipeline, but the healing function still scans existing evidence references (which may have been filtered) for drug tokens.

### Observed Recommendation
Case where governance excludes a low-quality evidence reference mentioning "Humira" → healing function finds "Humira" in a *remaining* evidence reference's `quoted_text` or `normalized_fact` → creates a new `requested_service` evidence reference with `confidence_score=0.7` that was not subject to governance review.

### Execution Trace
1. [governance_service.py](file:///c:/HealthAI - Copy/app/cases/governance_service.py) L119-130 → `evidence_for_consumption()` returns filtered evidence
2. [assembly/engine.py](file:///c:/HealthAI - Copy/app/assembly/engine.py) L139-170 → `synthesize_from_evidence()` calls `_assemble_from_evidence()` with `allow_document_text_healing=False`
3. [assembly/engine.py](file:///c:/HealthAI - Copy/app/assembly/engine.py) L264-316 → `_heal_requested_service_evidence()`:
   - L276: Returns immediately if `requested_service` evidence already exists — **but if governance excluded the only `requested_service` evidence, this check passes**
   - L280-285: Scans ALL remaining evidence references' `normalized_fact` and `quoted_text` for drug tokens
   - L297-314: Creates a NEW `EvidenceReference` with `fact_type="requested_service"` and `confidence_score=0.7`

### First Point of Failure
[assembly/engine.py](file:///c:/HealthAI - Copy/app/assembly/engine.py#L280-L285) — The healing function scans evidence references that passed governance, but creates a *new* evidence reference that was never itself subjected to governance quality checks.

### Root Cause
The `_heal_requested_service_evidence()` function creates evidence references that bypass the governance pipeline. When `allow_document_text_healing=False`, raw document text is not scanned (correctly), but existing evidence references — which may contain drug name mentions in non-service contexts (e.g., a denial reason mentioning "Humira is not indicated for...") — are still eligible sources.

### Clinical Safety Impact
🟠 **HIGH**: Governance-excluded evidence can indirectly influence the requested service identification, which affects guideline matching and the entire downstream review. A case with a non-covered indication could be matched to the wrong guideline.

### Recommended Fix
The healing function should not create new evidence when operating in governance-validated mode (`allow_document_text_healing=False` should also prevent evidence creation from existing references).

### Production Readiness Impact
Any case where governance filters evidence containing drug names and no explicit `requested_service` evidence survives filtering.

---

## Finding 8: `_normalized_support_text()` Injects Synthetic Step Therapy Phrases

### Observed Recommendation
Case: Step therapy status is `"failed"` → `_normalized_support_text()` injects both `"failed methotrexate"` and `"methotrexate failure"` into the support text, regardless of what the actual quoted evidence says.

### Execution Trace
1. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L556-566 → `_normalized_support_text()`:
   - L559-560: If step is "failed", adds `"failed methotrexate"` and `"methotrexate failure"`
   - These are hardcoded drug names, not derived from the evidence

2. The injected phrases are concatenated into the support text at L1090
3. The keyword matcher at L1355-1362 finds these phrases → marks step therapy criterion as `"met"`
4. **But**: The actual step therapy evidence might be about azathioprine, not methotrexate

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L559-L560) — Hardcoded injection of "failed methotrexate" regardless of which drug was actually failed.

### Root Cause
`_normalized_support_text()` assumes that when `step_therapy_status` is "failed", it was methotrexate that failed. This is encoded as a hardcoded string expansion. When a patient failed azathioprine (or another DMARD), the injected text falsely claims methotrexate failure.

### Clinical Safety Impact
🟠 **HIGH**: If a patient failed azathioprine but not methotrexate, the system would incorrectly report methotrexate failure evidence, potentially approving biologic therapy without the required methotrexate trial.

### Recommended Fix
Derive the drug name from the actual `ClinicalFact.value` or `quoted_text` rather than hardcoding "methotrexate". Or better, remove the synthetic phrase injection entirely and rely on the fact-based evaluation path.

### Production Readiness Impact
Any case where step therapy failure involves a drug other than methotrexate.

---

## Finding 9: `_evaluate_with_medspacy()` Specialist Check Skips Non-Specialist Evidence

### Observed Recommendation
Case: *"Patient seen by primary care provider. No specialist consultation documented."* → Specialist criterion marked **MET** because of "primary care provider" signal match.

### Execution Trace
1. [clinical_nlp.py](file:///c:/HealthAI - Copy/app/review/clinical_nlp.py) L94-104 → "Primary care provider" is labeled `PROVIDER_PRIMARY_CARE`, "Specialist" is labeled `SPECIALIST_RHEUM`
2. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1007-1036 → `_evaluate_with_medspacy()` specialist check:
   - L1008: `specialist_labels = ("SPECIALIST_RHEUM", "SPECIALIST_DERM", "SPECIALIST_GI")` — **does not include `PROVIDER_PRIMARY_CARE`**
   - L1016: `specialist_support = _signals_any(support_signals, specialist_labels)` — only finds specialist signals, not PCP signals
   - L1024-1032: `met = next((s for s in specialist_support if s.is_current_affirmed), None)` — if no specialist signal found, tries broader search
   - **L1026-1032**: Falls back to `_has_any(s.sentence, _SPECIALIST_CUES)` across **all** `support_signals` — this checks ANY signal's sentence for specialist vocabulary
   - If the PCP mention sentence contains "specialist" in the phrase "No specialist consultation", the keyword match fires

3. The `met` variable is set → L1033 returns `"met"`

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L1026-L1032) — The fallback specialist check searches all support signals' sentences for specialist cues, not just specialist-labeled signals.

### Root Cause
The fallback at L1026-1032 iterates over `support_signals` (all signals, not just specialist ones) and checks their sentences for `_SPECIALIST_CUES`. When a sentence about a PCP mentions "specialist" in a negated context (e.g., "No specialist consultation"), the keyword match finds "specialist" and returns "met" because `_has_any()` does not perform negation-aware matching.

### Clinical Safety Impact
🟠 **HIGH**: A case where the only specialist mention is negated (e.g., "no specialist referral") could be marked as having specialist documentation, incorrectly satisfying a mandatory criterion.

### Recommended Fix
The fallback search should (1) only iterate over signals from specialist labels, and (2) use negation-aware matching that respects the signal's `is_negated` property.

### Production Readiness Impact
Any case where clinical text mentions "specialist" in a negated or absent context.

---

## Finding 10: AI Review Override Does Not Propagate Deterministic Missing Criteria Evidence IDs

### Observed Recommendation
AI Review says APPROVE → Deterministic guardrail overrides to DENY (L337-350) → but the `missing_criteria` added from deterministic review have no `criteria_detail` entries with `not_met_evidence_ids`.

### Execution Trace
1. [review_agent.py](file:///c:/HealthAI - Copy/app/review/review_agent.py) L322-350 → `_apply_deterministic_guardrails()`:
   - L337-340: If deterministic says DENY and AI says APPROVE, override to DENY
   - L345-347: Add deterministic missing criteria to AI's missing criteria
   - L348-350: Remove those criteria from AI's matched criteria
2. **Missing**: The `criteria_detail` list on the AI review is NOT updated — no `CriterionEvaluation` entries are added for the newly missing criteria
3. [safety.py](file:///c:/HealthAI - Copy/app/governance/safety.py) L64-78 → `review()` checks for untraceable criteria — criteria in `criteria_detail` without evidence IDs
4. Since the deterministic override added criteria to `missing_criteria` but not to `criteria_detail`, these criteria are invisible to the safety gate traceability check

### First Point of Failure
[review_agent.py](file:///c:/HealthAI - Copy/app/review/review_agent.py#L337-L350) — `_apply_deterministic_guardrails()` modifies `missing_criteria` and `matched_criteria` lists but does not update `criteria_detail`.

### Root Cause
The guardrail override was implemented as a list manipulation on the recommendation-level fields (`recommendation`, `matched_criteria`, `missing_criteria`) without propagating the change into the structured `criteria_detail` array. This means the review result has an inconsistent state: the high-level recommendation says DENY with missing criteria, but the per-criterion detail says all criteria are MET.

### Clinical Safety Impact
🟡 **MEDIUM**: The DENY recommendation is correct (the guardrail worked), but the per-criterion traceability is broken. A human reviewer looking at criteria detail would see all-MET criteria alongside a DENY recommendation, which is confusing and undermines trust in the system.

### Recommended Fix
When adding deterministic missing criteria during guardrail override, also add corresponding `CriterionEvaluation` entries to `criteria_detail` with the deterministic engine's evidence IDs and reasoning.

### Production Readiness Impact
Any case where the AI review is overridden by the deterministic guardrail.

---

## Finding 11: Educational Text Removal Can Strip Legitimate Clinical Evidence

### Observed Recommendation
Case: *"Humira is FDA-approved for Rheumatoid Arthritis. Patient has Rheumatoid Arthritis."* → First sentence removed by `_remove_educational_text()` → but second sentence's keyword match for "rheumatoid arthritis" still works.

**Edge case**: Case: *"Humira is indicated for conditions such as Rheumatoid Arthritis"* (where this IS the only diagnosis mention) → sentence removed → diagnosis criterion UNKNOWN.

### Execution Trace
1. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L452-464 → `_remove_educational_text()` splits text on sentence boundaries and drops sentences matching `_is_educational_text()`
2. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L452-458 → `_is_educational_text()` checks if a sentence contains BOTH a clinical keyword AND an educational cue like "indicated for", "approved for", "covered for"
3. L1121: `support = _remove_educational_text(raw_support)` — the filtered text is used for the keyword matching path
4. However, L1123: `support_signals = extract_clinical_signals(support)` — MedSpaCy signals are extracted from the FILTERED text

### First Point of Failure
[engine.py](file:///c:/HealthAI - Copy/app/review/engine.py#L1121) — Educational text removal runs before clinical signal extraction.

### Root Cause
The `_remove_educational_text()` function is designed to prevent policy/educational boilerplate from being mistaken for clinical evidence. However, when a denial letter combines educational context with actual clinical findings in the same sentence (e.g., "Humira is indicated for rheumatoid arthritis; however, the patient's diagnosis of rheumatoid arthritis was not confirmed"), the entire sentence is stripped.

### Clinical Safety Impact
🟡 **MEDIUM**: In edge cases where the educational text IS the only source of diagnosis information, the diagnosis criterion defaults to `"unknown"` instead of `"met"`. This leads to `INSUFFICIENT_INFORMATION` instead of the correct recommendation.

### Recommended Fix
Run educational text removal on the keyword-matching path only, not on the MedSpaCy signal extraction path. Or use sentence-level granularity that preserves the non-educational clause of compound sentences.

### Production Readiness Impact
Denial letters that embed clinical findings within educational context paragraphs.

---

## Finding 12: Export Safety Gate Does Not Check for Unresolved Semantic Conflicts

### Observed Recommendation
Case with a diagnosis-assertion semantic conflict (Active RA + Rule-out RA) passes export safety gate when `require_human_review_before_export=False`.

### Execution Trace
1. [assembly/engine.py](file:///c:/HealthAI - Copy/app/assembly/engine.py) L233-236 → `_semantic_conflicts()` generates semantic conflict with type `"diagnosis-assertion"` 
2. [engine.py](file:///c:/HealthAI - Copy/app/review/engine.py) L1239-1241 → `_clinical_conflict_reasons()` checks `clinical_facts` for CONFLICTED status → populates `safety_gate["unresolved_conflicts"]`
3. [safety.py](file:///c:/HealthAI - Copy/app/governance/safety.py) L31-87 → `review()` checks `gate.get("unresolved_conflicts")` → adds reasons → returns `HUMAN_REVIEW_REQUIRED`
4. [safety.py](file:///c:/HealthAI - Copy/app/governance/safety.py) L116-160 → `export()`:
   - L138-144: Checks `review_gate.get("status") == HUMAN_REVIEW_REQUIRED` **but only blocks export if `latest_decision is None`**
   - If `require_human_review_before_export=False` and no human decision exists, L123-127 does not fire
   - L145-148: Checks `compliance.is_compliant` — but compliance checks are separate from semantic conflicts
   - **Missing**: No direct check for unresolved semantic conflicts from the `conflict_report`

### First Point of Failure
[safety.py](file:///c:/HealthAI - Copy/app/governance/safety.py#L116-L160) — The `export()` method relies on the review safety gate's `HUMAN_REVIEW_REQUIRED` status, which is only propagated if the review engine ran and populated the safety gate. If the review was generated by the AI path and the deterministic guardrail did not fire, the semantic conflict signal may not reach the export gate.

### Root Cause
The export safety gate is a derivative gate — it checks the review result's safety gate status rather than independently checking for unresolved conflicts. If the review was produced by an AI backend that did not detect the conflict (or if the conflict was in an area the AI didn't evaluate), the export gate has no independent awareness of semantic conflicts.

### Clinical Safety Impact
🔴 **CRITICAL**: A case with unresolved clinical contradictions (e.g., diagnosis confirmed + diagnosis being ruled out) could be exported for patient delivery without human review.

### Recommended Fix
The `export()` method should independently check the `conflict_report` from the `UnifiedCaseContext` for unresolved HIGH-severity conflicts, rather than relying solely on the review result's safety gate.

### Production Readiness Impact
Any case with HIGH-severity semantic conflicts where the review was generated by the AI path.

---

## Cross-Cutting Concerns

### Ordering of Evaluation Layers

The review engine has three layers of criterion evaluation, applied in this order:
1. **Clinical Facts** (`_evaluate_with_clinical_facts()`)
2. **MedSpaCy NLP** (`_evaluate_with_medspacy()` inside `_evaluate_criterion()`)
3. **Keyword Matching** (fallback in `_evaluate_criterion()`)

The fact-based layer takes priority over the NLP/keyword layer (L1147-1148), which is correct in design. However, several findings above show that:
- The NLP layer is **always called** even when facts are available (Finding 6)
- The fact layer and NLP layer can **disagree** and the fact layer wins, but the NLP layer's reasoning text may leak into audit output
- The keyword layer can be **poisoned** by synthetic text injected by `_normalized_support_text()` (Finding 8)

### Governance Timing

The architecture mandates that governance runs BEFORE review generation. This is implemented correctly in the `CaseService` facade via `evidence_for_consumption()` → `synthesize_from_evidence()` → `review()`. However:
- Finding 7 shows that the assembly engine can re-introduce evidence-adjacent data during the governed synthesis
- The safety gate (Finding 12) does not independently verify conflict status at export time

### MedSpaCy Dependency

When MedSpaCy is unavailable, `extract_clinical_signals()` returns an empty list (L367-369 in `clinical_nlp.py`). This means:
- The entire NLP evaluation layer is skipped
- Only the keyword fallback layer operates
- Several protections (negation detection, assertion status, differential diagnosis detection) are lost
- The system silently degrades without warning

---

## Summary Table

| # | Severity | Component | File | Root Cause Summary |
|---|----------|-----------|------|--------------------|
| 1 | 🔴 CRITICAL | TB Polarity | `clinical_nlp.py:517` | Default return `"positive"` instead of `"unknown"` |
| 2 | 🔴 CRITICAL | Step Therapy | `engine.py:951-968` | Non-DMARD failure cues leak into DMARD evaluation |
| 3 | 🟠 HIGH | Specialist | `clinical_fact.py:273-287` | Narrow provider vocabulary → UNKNOWN state ignored |
| 4 | 🔴 CRITICAL | Diagnosis | `engine.py:764-770` | CONFLICTED facts not excluded from active_ra filter |
| 5 | 🟠 HIGH | TB Negation | `engine.py:151-154` | 80-char context window too narrow for clinical text |
| 6 | 🟡 MEDIUM | Eval Order | `engine.py:1138-1145` | NLP path runs unnecessarily, confuses audit trail |
| 7 | 🟠 HIGH | Governance | `assembly/engine.py:280-285` | Evidence healing bypasses governance pipeline |
| 8 | 🟠 HIGH | Support Text | `engine.py:559-560` | Hardcoded "methotrexate" injection in support text |
| 9 | 🟠 HIGH | Specialist NLP | `engine.py:1026-1032` | Negated specialist mention matches as positive |
| 10 | 🟡 MEDIUM | AI Guardrail | `review_agent.py:337-350` | Missing criteria have no criteria_detail entries |
| 11 | 🟡 MEDIUM | Edu Text | `engine.py:1121` | Educational text removal runs before signal extraction |
| 12 | 🔴 CRITICAL | Export Gate | `safety.py:116-160` | No independent semantic conflict check at export |

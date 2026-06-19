# HealthAI Master Clinical Validation Report

Generated: 2026-06-16T15:04:05.484832+00:00

## Executive Summary

- Total Cases: 10
- Passed: 1
- Failed: 9
- Pass Percentage: 10.0%

### Backend Performance

- Local Accuracy: 40.0%
- Gemini Accuracy: 0.0%
- Gemini Cases Run: 0
- Gemini Cases Unavailable/Skipped: 10

### Safety Metrics

- Human Review Compliance: 75.0%
- Conflict Detection Success Rate: 100.0%
- Traceability Success Rate: 100.0%
- Governance Compliance Rate: 90.0%

## Per-Case Results

### HUMIRA-011 - PCP Mismatch Deny

- Expected Outcome: DENY
- Local Outcome: INSUFFICIENT_INFORMATION
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: SKIPPED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: provider_state drift: expected NON_SPECIALIST, got CONFLICT.
  - [HIGH] Clinical Review: Local outcome drift: expected DENY, got HUMAN_REVIEW.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-011",
    "scenario": "PCP Mismatch Deny",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "provider_state drift: expected NON_SPECIALIST, got CONFLICT.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "NON_SPECIALIST",
    "actual": "CONFLICT"
  },
  {
    "case_id": "HUMIRA-011",
    "scenario": "PCP Mismatch Deny",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected DENY, got HUMAN_REVIEW.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-012 - Insufficient Step Therapy Duration

- Expected Outcome: DENY
- Local Outcome: APPROVE
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: APPROVE
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: step_therapy_state drift: expected INTOLERANT, got FAILED.
  - [HIGH] Clinical Review: Local outcome drift: expected DENY, got APPROVE.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-012",
    "scenario": "Insufficient Step Therapy Duration",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "step_therapy_state drift: expected INTOLERANT, got FAILED.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "INTOLERANT",
    "actual": "FAILED"
  },
  {
    "case_id": "HUMIRA-012",
    "scenario": "Insufficient Step Therapy Duration",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected DENY, got APPROVE.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-013 - Active Severe Infection Contraindication

- Expected Outcome: DENY
- Local Outcome: DENY
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: tb_state drift: expected NEGATIVE, got UNKNOWN.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-013",
    "scenario": "Active Severe Infection Contraindication",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "tb_state drift: expected NEGATIVE, got UNKNOWN.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "NEGATIVE",
    "actual": "UNKNOWN"
  }
]
```

### HUMIRA-014 - Dual Biologic Duplication Gate

- Expected Outcome: DENY
- Local Outcome: INSUFFICIENT_INFORMATION
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: SKIPPED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] Clinical Review: Local outcome drift: expected DENY, got HUMAN_REVIEW.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-014",
    "scenario": "Dual Biologic Duplication Gate",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected DENY, got HUMAN_REVIEW.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-015 - Step Therapy Exception Permitted

- Expected Outcome: HUMAN_REVIEW
- Local Outcome: APPROVE
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: APPROVE
- Pass/Fail: FAIL
- Issues Found:
  - [CRITICAL] Human Review Escalation: Expected human-review routing was not honored.
  - [HIGH] ClinicalFact: step_therapy_state drift: expected CONTRAINDICATED, got FAILED.
  - [HIGH] Clinical Review: Local outcome drift: expected HUMAN_REVIEW, got APPROVE.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-015",
    "scenario": "Step Therapy Exception Permitted",
    "subsystem": "Human Review Escalation",
    "severity": "CRITICAL",
    "message": "Expected human-review routing was not honored.",
    "root_cause_hypothesis": "Governance or safety gate did not enforce fail-closed routing.",
    "recommended_fix": "Fail closed on unresolved medium/high conflicts.",
    "fix_complexity": "Moderate"
  },
  {
    "case_id": "HUMIRA-015",
    "scenario": "Step Therapy Exception Permitted",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "step_therapy_state drift: expected CONTRAINDICATED, got FAILED.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "CONTRAINDICATED",
    "actual": "FAILED"
  },
  {
    "case_id": "HUMIRA-015",
    "scenario": "Step Therapy Exception Permitted",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected HUMAN_REVIEW, got APPROVE.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-016 - Missing Objective Diagnostic Metric

- Expected Outcome: DENY
- Local Outcome: APPROVE
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: APPROVE
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] Clinical Review: Local outcome drift: expected DENY, got APPROVE.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-016",
    "scenario": "Missing Objective Diagnostic Metric",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected DENY, got APPROVE.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-017 - Diagnosis Ambiguity Differential

- Expected Outcome: HUMAN_REVIEW
- Local Outcome: DENY
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: diagnosis_state drift: expected CONFLICT, got RULE_OUT.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-017",
    "scenario": "Diagnosis Ambiguity Differential",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "diagnosis_state drift: expected CONFLICT, got RULE_OUT.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "CONFLICT",
    "actual": "RULE_OUT"
  }
]
```

### HUMIRA-018 - Varying Step Therapy Outcomes Conflict

- Expected Outcome: HUMAN_REVIEW
- Local Outcome: INSUFFICIENT_INFORMATION
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: SKIPPED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: PASS
- Issues Found:
  - None

### HUMIRA-019 - Latent TB Treatment Clearance

- Expected Outcome: APPROVE
- Local Outcome: DENY
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: GENERATED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: tb_state drift: expected NEGATIVE, got POSITIVE.
  - [HIGH] ClinicalFact: step_therapy_state drift: expected FAILED, got UNKNOWN.
  - [HIGH] Clinical Review: Local outcome drift: expected APPROVE, got HUMAN_REVIEW.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-019",
    "scenario": "Latent TB Treatment Clearance",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "tb_state drift: expected NEGATIVE, got POSITIVE.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "NEGATIVE",
    "actual": "POSITIVE"
  },
  {
    "case_id": "HUMIRA-019",
    "scenario": "Latent TB Treatment Clearance",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "step_therapy_state drift: expected FAILED, got UNKNOWN.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "FAILED",
    "actual": "UNKNOWN"
  },
  {
    "case_id": "HUMIRA-019",
    "scenario": "Latent TB Treatment Clearance",
    "subsystem": "Clinical Review",
    "severity": "HIGH",
    "message": "Local outcome drift: expected APPROVE, got HUMAN_REVIEW.",
    "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
    "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
    "fix_complexity": "Moderate"
  }
]
```

### HUMIRA-020 - Payer vs Clinical Provider Conflict

- Expected Outcome: HUMAN_REVIEW
- Local Outcome: DENY
- Gemini Outcome: UNAVAILABLE
- Appeal Outcome: SKIPPED
- Workflow Decision: HUMAN_REVIEW
- Pass/Fail: FAIL
- Issues Found:
  - [HIGH] ClinicalFact: diagnosis_state drift: expected CONFLICT, got ACTIVE.

Relevant JSON snippet:

```json
[
  {
    "case_id": "HUMIRA-020",
    "scenario": "Payer vs Clinical Provider Conflict",
    "subsystem": "ClinicalFact",
    "severity": "HIGH",
    "message": "diagnosis_state drift: expected CONFLICT, got ACTIVE.",
    "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
    "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
    "fix_complexity": "Surgical",
    "expected": "CONFLICT",
    "actual": "ACTIVE"
  }
]
```

## What Is Working

### Extraction
- Source-backed evidence references were extracted.

### Assembly
- Multi-document cases assembled into unified contexts.

### ClinicalFact
- ClinicalFact records were generated for executed cases.

### Conflict Detection
- Semantic conflicts routed to human review.

### Clinical Review
- Local review produced structured criterion-level outputs.

### Appeals
- Appeals generated and verification metadata was attached.

### Governance
- Governance compliance and export gates executed.

### Explainability
- Traceability chains were generated.

## What Needs Work

### Human Review Escalation
- Severity: CRITICAL
  - Case: HUMIRA-015 - Step Therapy Exception Permitted
  - Root Cause Hypothesis: Governance or safety gate did not enforce fail-closed routing.
  - Recommended Fix: Fail closed on unresolved medium/high conflicts.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Human Review Escalation",
  "severity": "CRITICAL",
  "message": "Expected human-review routing was not honored.",
  "root_cause_hypothesis": "Governance or safety gate did not enforce fail-closed routing.",
  "recommended_fix": "Fail closed on unresolved medium/high conflicts.",
  "fix_complexity": "Moderate"
}
```

### Clinical Review
- Severity: HIGH
  - Case: HUMIRA-011 - PCP Mismatch Deny
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected DENY, got HUMAN_REVIEW.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```
- Severity: HIGH
  - Case: HUMIRA-012 - Insufficient Step Therapy Duration
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected DENY, got APPROVE.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```
- Severity: HIGH
  - Case: HUMIRA-014 - Dual Biologic Duplication Gate
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected DENY, got HUMAN_REVIEW.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```
- Severity: HIGH
  - Case: HUMIRA-015 - Step Therapy Exception Permitted
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected HUMAN_REVIEW, got APPROVE.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```
- Severity: HIGH
  - Case: HUMIRA-016 - Missing Objective Diagnostic Metric
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected DENY, got APPROVE.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```
- Severity: HIGH
  - Case: HUMIRA-019 - Latent TB Treatment Clearance
  - Root Cause Hypothesis: Local review or workflow routing disagreed with expected outcome.
  - Recommended Fix: Make the criterion consume ClinicalFact evidence instead of legacy text.
  - Fix Complexity: Moderate
  - Failure JSON:

```json
{
  "subsystem": "Clinical Review",
  "severity": "HIGH",
  "message": "Local outcome drift: expected APPROVE, got HUMAN_REVIEW.",
  "root_cause_hypothesis": "Local review or workflow routing disagreed with expected outcome.",
  "recommended_fix": "Make the criterion consume ClinicalFact evidence instead of legacy text.",
  "fix_complexity": "Moderate"
}
```

### ClinicalFact
- Severity: HIGH
  - Case: HUMIRA-011 - PCP Mismatch Deny
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "provider_state drift: expected NON_SPECIALIST, got CONFLICT.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-012 - Insufficient Step Therapy Duration
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "step_therapy_state drift: expected INTOLERANT, got FAILED.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-013 - Active Severe Infection Contraindication
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "tb_state drift: expected NEGATIVE, got UNKNOWN.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-015 - Step Therapy Exception Permitted
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "step_therapy_state drift: expected CONTRAINDICATED, got FAILED.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-017 - Diagnosis Ambiguity Differential
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "diagnosis_state drift: expected CONFLICT, got RULE_OUT.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-019 - Latent TB Treatment Clearance
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "tb_state drift: expected NEGATIVE, got POSITIVE.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-019 - Latent TB Treatment Clearance
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "step_therapy_state drift: expected FAILED, got UNKNOWN.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```
- Severity: HIGH
  - Case: HUMIRA-020 - Payer vs Clinical Provider Conflict
  - Root Cause Hypothesis: ClinicalFact generation or state normalization drift.
  - Recommended Fix: Normalize expected state through the ClinicalFact contract before review.
  - Fix Complexity: Surgical
  - Failure JSON:

```json
{
  "subsystem": "ClinicalFact",
  "severity": "HIGH",
  "message": "diagnosis_state drift: expected CONFLICT, got ACTIVE.",
  "root_cause_hypothesis": "ClinicalFact generation or state normalization drift.",
  "recommended_fix": "Normalize expected state through the ClinicalFact contract before review.",
  "fix_complexity": "Surgical"
}
```

## AI vs Local Divergence Analysis

No Local/Gemini recommendation divergence was captured.

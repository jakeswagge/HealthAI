# Fix Duplicate Streamlit Keys — Bugfix Design

## Overview

The dashboard crashes on load with `StreamlitDuplicateElementKey` because
`select_or_create_case()` in `app/ui/tabs/common.py` uses two hardcoded Streamlit widget
keys (`assembly_case_select`, `assembly_new_case`). Streamlit renders every tab body on
every rerun, so both `render_document_assembly_tab()` and `render_document_ingestion_tab()`
invoke the function in the same render pass, registering duplicate keys.

The fix is minimal and surgical: add a `key_prefix: str = "assembly"` parameter to
`select_or_create_case()` and interpolate it into both widget keys. The two call sites pass
`key_prefix="assembly"` (assembly tab — no key change) and `key_prefix="ingestion"`
(ingestion tab — new unique keys). No logic, state, or UI behavior changes.

## Glossary

- **Bug_Condition (C)**: Both `render_document_assembly_tab()` and
  `render_document_ingestion_tab()` call `select_or_create_case()` in the same Streamlit
  render pass, each registering widgets with the same hardcoded keys.
- **Property (P)**: After the fix, every widget key registered in a single render pass is
  unique; no `StreamlitDuplicateElementKey` is raised.
- **Preservation**: All existing widget behavior, session-state interactions, and UI
  appearance for both tabs remain identical to the pre-fix state.
- **`select_or_create_case(service)`**: The shared helper in `app/ui/tabs/common.py` that
  renders a case-selection selectbox and a "create new case" button.
- **`key_prefix`**: The new string parameter that namespaces widget keys per call site.
- **Streamlit render pass**: A single top-to-bottom execution of the dashboard script
  triggered by any user interaction or page load; all tab bodies execute regardless of which
  tab is visible.

## Bug Details

### Bug Condition

The bug manifests whenever the Streamlit dashboard script executes a full render pass (i.e.,
on every page load or user interaction). During that pass, both
`render_document_assembly_tab()` and `render_document_ingestion_tab()` call
`select_or_create_case(service)`, each attempting to register an `st.selectbox` with
`key="assembly_case_select"` and an `st.button` with `key="assembly_new_case"`. Streamlit
detects the duplicate keys and raises `StreamlitDuplicateElementKey` before any tab content
is displayed.

**Formal Specification:**

```
FUNCTION isBugCondition(renderContext)
  INPUT: renderContext — a Streamlit render pass that includes both
         render_document_assembly_tab() and render_document_ingestion_tab()
  OUTPUT: boolean

  assemblyCallsSelectOrCreate  ← render_document_assembly_tab calls select_or_create_case
  ingestionCallsSelectOrCreate ← render_document_ingestion_tab calls select_or_create_case
  keysAreIdentical             ← both calls use the same hardcoded key strings

  RETURN assemblyCallsSelectOrCreate
         AND ingestionCallsSelectOrCreate
         AND keysAreIdentical
END FUNCTION
```

### Examples

- **Page load (any state)**: Dashboard loads → both tabs render → duplicate
  `assembly_case_select` key → `StreamlitDuplicateElementKey` crash. Expected: no crash,
  both tabs render their case-selection widgets normally.
- **Tab switch**: User clicks any tab → full rerun → same crash. Expected: smooth rerun,
  correct tab content displayed.
- **Button click**: User clicks any button → full rerun → same crash. Expected: button
  action executes, page reruns cleanly.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The Document Assembly tab's selectbox must continue to use key `assembly_case_select` and
  the button must continue to use key `assembly_new_case` (no session-state migration
  needed).
- The Document Ingestion tab's case-selection widget must behave identically to before,
  just under new keys (`ingestion_case_select`, `ingestion_new_case`).
- Calling `select_or_create_case(service)` without a `key_prefix` argument must continue to
  work (default `"assembly"` preserves backward compatibility).
- All other tabs and UI components must be completely unaffected.

**Scope:**
All render passes that do NOT involve both tabs calling `select_or_create_case()` with
identical keys are unaffected. This includes:
- Any tab other than Document Assembly and Document Ingestion.
- Any call to `select_or_create_case()` with a unique `key_prefix`.
- All session-state reads/writes unrelated to these two widget keys.

## Hypothesized Root Cause

1. **Hardcoded widget keys**: `select_or_create_case()` passes literal strings
   `"assembly_case_select"` and `"assembly_new_case"` to `st.selectbox` and `st.button`.
   There is no mechanism to differentiate keys across call sites.

2. **Streamlit's all-tabs-render behavior**: Unlike frameworks that lazily render only the
   visible tab, Streamlit executes every `with <tab>:` block on every rerun. Both tab render
   functions therefore run unconditionally in the same script execution.

3. **Shared helper without namespacing**: The helper was originally written for a single
   call site (assembly tab). When the ingestion tab was added and reused the same helper,
   no key namespacing was introduced.

## Correctness Properties

Property 1: Bug Condition - Duplicate Key Crash on Dashboard Load

_For any_ Streamlit render pass where both `render_document_assembly_tab()` and
`render_document_ingestion_tab()` execute (i.e., isBugCondition returns true), the fixed
`select_or_create_case()` function SHALL register widgets with unique keys for each call
site, so that no `StreamlitDuplicateElementKey` exception is raised and both tabs render
their case-selection UI successfully.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Existing Widget Behavior Unchanged

_For any_ interaction with the Document Assembly tab's case-selection widgets, and for any
interaction with the Document Ingestion tab's case-selection widgets, the fixed function
SHALL produce the same session-state updates and UI behavior as the original function,
preserving all existing case-selection and case-creation functionality.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

**File**: `app/ui/tabs/common.py`

**Function**: `select_or_create_case`

**Specific Changes**:

1. **Add `key_prefix` parameter**: Change the signature from
   `select_or_create_case(service: CaseService)` to
   `select_or_create_case(service: CaseService, key_prefix: str = "assembly")`.

2. **Namespace the selectbox key**: Replace `key="assembly_case_select"` with
   `key=f"{key_prefix}_case_select"`.

3. **Namespace the button key**: Replace `key="assembly_new_case"` with
   `key=f"{key_prefix}_new_case"`.

---

**File**: `app/ui/tabs/assembly_tabs.py`

**Function**: `render_document_assembly_tab`

**Specific Changes**:

4. **Pass explicit prefix**: Change `select_or_create_case(service)` to
   `select_or_create_case(service, key_prefix="assembly")`. This keeps the existing key
   names unchanged — no session-state migration required.

---

**File**: `app/ui/tabs/ingestion_tabs.py`

**Function**: `render_document_ingestion_tab`

**Specific Changes**:

5. **Pass unique prefix**: Change `select_or_create_case(service)` to
   `select_or_create_case(service, key_prefix="ingestion")`. This gives the ingestion tab
   unique keys (`ingestion_case_select`, `ingestion_new_case`), resolving the collision.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that
demonstrate the bug on unfixed code, then verify the fix works correctly and preserves
existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix.
Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write a test that simulates a Streamlit render pass by calling both
`render_document_assembly_tab()` and `render_document_ingestion_tab()` (or directly calling
`select_or_create_case()` twice with the same service instance) and assert that no
`StreamlitDuplicateElementKey` is raised. Run this test on the UNFIXED code to observe the
failure and confirm the root cause.

**Test Cases**:

1. **Dual-tab render test**: Call `select_or_create_case(service)` twice in the same test
   (simulating both tabs) and assert no duplicate-key exception is raised. (Will FAIL on
   unfixed code.)
2. **Direct key collision test**: Inspect the keys passed to `st.selectbox` and `st.button`
   in two consecutive calls to `select_or_create_case(service)` and assert they are
   distinct. (Will FAIL on unfixed code — both calls use identical hardcoded keys.)

**Expected Counterexamples**:
- `StreamlitDuplicateElementKey: There are multiple widgets with the same key='assembly_case_select'`
- Possible causes: hardcoded key strings, no namespacing mechanism, Streamlit renders all
  tabs unconditionally.

### Fix Checking

**Goal**: Verify that for all render passes where the bug condition holds, the fixed function
produces unique widget keys and no exception is raised.

**Pseudocode:**
```
FOR ALL renderContext WHERE isBugCondition(renderContext) DO
  result := select_or_create_case_fixed(service, key_prefix="assembly")
             AND select_or_create_case_fixed(service, key_prefix="ingestion")
  ASSERT no StreamlitDuplicateElementKey raised
  ASSERT keys registered are {"assembly_case_select", "assembly_new_case",
                               "ingestion_case_select", "ingestion_new_case"}
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (single call site,
or calls with distinct prefixes), the fixed function produces the same result as the original
function.

**Pseudocode:**
```
FOR ALL renderContext WHERE NOT isBugCondition(renderContext) DO
  ASSERT select_or_create_case_original(service) = select_or_create_case_fixed(service, key_prefix="assembly")
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking
because:
- It generates many test cases automatically across the input domain (various case lists,
  session states).
- It catches edge cases that manual unit tests might miss (empty case list, many cases,
  special characters in case IDs).
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs.

**Test Plan**: Observe behavior of `select_or_create_case(service)` on UNFIXED code for
single-call scenarios, then write property-based tests capturing that behavior.

**Test Cases**:

1. **Single-call preservation**: Verify that calling `select_or_create_case(service,
   key_prefix="assembly")` on fixed code produces the same widget keys and return value as
   calling `select_or_create_case(service)` on unfixed code.
2. **Default prefix preservation**: Verify that `select_or_create_case(service)` (no
   explicit prefix) on fixed code behaves identically to the original (default `"assembly"`
   prefix).
3. **Return value preservation**: For any case list state, verify the return value
   (selected case ID or None) is identical between original and fixed code when called with
   `key_prefix="assembly"`.

### Unit Tests

- Test that `select_or_create_case(service, key_prefix="assembly")` registers keys
  `assembly_case_select` and `assembly_new_case`.
- Test that `select_or_create_case(service, key_prefix="ingestion")` registers keys
  `ingestion_case_select` and `ingestion_new_case`.
- Test that calling the function twice with different prefixes in the same render pass does
  not raise `StreamlitDuplicateElementKey`.
- Test that the default `key_prefix="assembly"` is used when no prefix is supplied.

### Property-Based Tests

- Generate random `key_prefix` strings and verify the resulting widget keys always follow
  the pattern `{key_prefix}_case_select` and `{key_prefix}_new_case`.
- Generate random case lists and verify the return value of the fixed function (with
  `key_prefix="assembly"`) matches the original function across all inputs.
- Generate pairs of distinct `key_prefix` values and verify the resulting key sets are
  always disjoint (no collisions possible).

### Integration Tests

- Simulate a full dashboard render pass (both assembly and ingestion tabs) and verify no
  `StreamlitDuplicateElementKey` is raised.
- Verify the Document Assembly tab still selects and creates cases correctly after the fix.
- Verify the Document Ingestion tab still selects and creates cases correctly after the fix.
- Run the full test suite (`python -m pytest app/tests/ -v`) and confirm all existing tests
  pass.

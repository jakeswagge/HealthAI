# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Duplicate Key Crash on Dashboard Load
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the duplicate-key crash
  - **Scoped PBT Approach**: Scope the property to the concrete failing case — two calls to `select_or_create_case(service)` in the same render pass (no prefix argument)
  - Create a test file `app/tests/test_select_or_create_case_keys.py`
  - Use `streamlit.testing.v1.AppTest` or mock `st.selectbox` / `st.button` to capture the `key` arguments passed in each call
  - Call `select_or_create_case(service)` twice (simulating assembly tab + ingestion tab) and assert that the two selectbox keys are distinct and the two button keys are distinct
  - Run test on UNFIXED code: `python -m pytest app/tests/test_select_or_create_case_keys.py -v`
  - **EXPECTED OUTCOME**: Test FAILS with assertion error showing both calls use `assembly_case_select` / `assembly_new_case` (proves the bug exists)
  - Document the counterexample: `select_or_create_case(service)` called twice → keys `{'assembly_case_select', 'assembly_new_case'}` registered twice → `StreamlitDuplicateElementKey`
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Existing Widget Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code: `select_or_create_case(service)` with a non-empty case list returns the selected case ID; with an empty list and no button press returns `None`
  - Observe on UNFIXED code: the selectbox key is `assembly_case_select` and the button key is `assembly_new_case`
  - Write property-based tests (use `hypothesis` or parametrize with varied case lists) in `app/tests/test_select_or_create_case_keys.py`:
    - For any case list, `select_or_create_case(service, key_prefix="assembly")` on fixed code returns the same value as `select_or_create_case(service)` on unfixed code
    - For any case list, calling with no `key_prefix` argument uses keys `assembly_case_select` and `assembly_new_case` (default preserved)
    - For any `key_prefix` value, the resulting keys follow the pattern `{key_prefix}_case_select` and `{key_prefix}_new_case`
  - Run tests on UNFIXED code: `python -m pytest app/tests/test_select_or_create_case_keys.py::test_preservation -v`
  - **EXPECTED OUTCOME**: Tests PASS (confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Fix duplicate Streamlit widget keys in `select_or_create_case`

  - [x] 3.1 Implement the fix
    - In `app/ui/tabs/common.py`: add `key_prefix: str = "assembly"` parameter to `select_or_create_case(service: CaseService)`
    - Replace `key="assembly_case_select"` with `key=f"{key_prefix}_case_select"`
    - Replace `key="assembly_new_case"` with `key=f"{key_prefix}_new_case"`
    - In `app/ui/tabs/assembly_tabs.py`: update call to `select_or_create_case(service, key_prefix="assembly")` (explicit, no behavior change)
    - In `app/ui/tabs/ingestion_tabs.py`: update call to `select_or_create_case(service, key_prefix="ingestion")` (unique keys, resolves collision)
    - _Bug_Condition: isBugCondition(renderContext) — both tabs call select_or_create_case with identical hardcoded keys in the same render pass_
    - _Expected_Behavior: each call site registers unique keys {prefix}_case_select and {prefix}_new_case; no StreamlitDuplicateElementKey raised_
    - _Preservation: default key_prefix="assembly" preserves existing assembly-tab key names; ingestion-tab behavior unchanged except for key strings_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Duplicate Key Crash on Dashboard Load
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (two calls with distinct prefixes → distinct keys → no crash)
    - Run: `python -m pytest app/tests/test_select_or_create_case_keys.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms the duplicate-key bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** - Existing Widget Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run: `python -m pytest app/tests/test_select_or_create_case_keys.py -v`
    - **EXPECTED OUTCOME**: All preservation tests PASS (confirms no regressions in widget behavior or session state)
    - Confirm default `key_prefix="assembly"` still produces keys `assembly_case_select` and `assembly_new_case`

- [x] 4. Checkpoint — Ensure all tests pass
  - Run the full test suite: `python -m pytest app/tests/ -v`
  - All existing tests must pass; no new failures introduced
  - Confirm the two new test cases (exploration + preservation) both pass
  - Ask the user if any questions arise before closing the spec

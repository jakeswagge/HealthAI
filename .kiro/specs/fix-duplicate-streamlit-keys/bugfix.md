# Bugfix Requirements Document

## Introduction

The HealthAI Streamlit dashboard crashes with a `StreamlitDuplicateElementKey` error on the
key `assembly_case_select` every time the dashboard loads. The crash is caused by
`select_or_create_case()` in `app/ui/tabs/common.py` using two hardcoded widget keys
(`assembly_case_select` and `assembly_new_case`). Because Streamlit renders all tab bodies on
every rerun — not just the active tab — both `render_document_assembly_tab()` and
`render_document_ingestion_tab()` call this function in the same render pass, producing two
widgets with identical keys and triggering the exception. The fix adds a `key_prefix`
parameter to namespace the keys per call site, making them unique without changing any other
behavior.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the dashboard loads and Streamlit renders all tab bodies in a single pass THEN the
system raises `StreamlitDuplicateElementKey` for key `assembly_case_select` because
`select_or_create_case()` is called from both `render_document_assembly_tab()` and
`render_document_ingestion_tab()` with the same hardcoded key.

1.2 WHEN the dashboard loads and Streamlit renders all tab bodies in a single pass THEN the
system raises `StreamlitDuplicateElementKey` for key `assembly_new_case` because the same
hardcoded key is used for the "Create new multi-document case" button in both tab render
functions.

### Expected Behavior (Correct)

2.1 WHEN the dashboard loads and both tab render functions call `select_or_create_case()` in
the same render pass THEN the system SHALL render each widget with a unique key (namespaced
by call site) so that no `StreamlitDuplicateElementKey` error is raised.

2.2 WHEN `select_or_create_case()` is called with `key_prefix="assembly"` from
`render_document_assembly_tab()` THEN the system SHALL use widget keys
`assembly_case_select` and `assembly_new_case`, preserving the existing key names and
session-state behavior for that tab.

2.3 WHEN `select_or_create_case()` is called with `key_prefix="ingestion"` from
`render_document_ingestion_tab()` THEN the system SHALL use widget keys
`ingestion_case_select` and `ingestion_new_case`, making those keys unique and distinct from
the assembly tab's keys.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a user interacts with the "Target case" selectbox or "Create new multi-document
case" button in the Document Assembly tab THEN the system SHALL CONTINUE TO update case
selection and session state exactly as before the fix.

3.2 WHEN a user interacts with the "Target case" selectbox or "Create new multi-document
case" button in the Document Ingestion tab THEN the system SHALL CONTINUE TO update case
selection and session state exactly as before the fix.

3.3 WHEN `select_or_create_case()` is called without an explicit `key_prefix` argument THEN
the system SHALL CONTINUE TO default to `key_prefix="assembly"`, preserving backward
compatibility with any other existing call sites.

3.4 WHEN any tab other than Document Assembly or Document Ingestion is rendered THEN the
system SHALL CONTINUE TO function without errors or behavioral changes.

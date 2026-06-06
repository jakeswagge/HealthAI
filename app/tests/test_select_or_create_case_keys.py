"""Bug condition exploration test for duplicate Streamlit widget keys.

Property 1: Bug Condition — Duplicate Key Crash on Dashboard Load

This test simulates a Streamlit render pass where both the Document Assembly
tab and the Document Ingestion tab call ``select_or_create_case(service)``.
It captures the ``key`` arguments passed to ``st.selectbox`` and ``st.button``
in each call and asserts that the two selectbox keys are distinct and the two
button keys are distinct.

**EXPECTED OUTCOME on UNFIXED code**: FAIL — both calls use the same hardcoded
keys ``assembly_case_select`` and ``assembly_new_case``, proving the bug exists.

**Validates: Requirements 1.1, 1.2**
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from app.ui.tabs.common import select_or_create_case


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(case_ids: list[str] | None = None) -> MagicMock:
    """Return a minimal mock CaseService whose list_cases() returns stub records."""
    service = MagicMock()
    if case_ids:
        records = [MagicMock(case_id=cid) for cid in case_ids]
    else:
        records = []
    service.list_cases.return_value = records
    return service


# ---------------------------------------------------------------------------
# Property 1: Bug Condition — keys must be distinct across two calls
# ---------------------------------------------------------------------------

class TestBugConditionDuplicateKeys:
    """Simulate a dual-tab render pass and assert widget keys are unique.

    On UNFIXED code both calls use the same hardcoded keys, so the assertion
    fails — confirming the bug exists.
    """

    def test_selectbox_keys_are_distinct_across_two_calls(self):
        """Two calls with distinct key_prefix values must register distinct selectbox keys.

        Simulates the fixed calling convention: assembly tab passes
        key_prefix="assembly", ingestion tab passes key_prefix="ingestion".

        **Validates: Requirements 1.1, 2.1, 2.2, 2.3**
        """
        service = _make_service(["case-001", "case-002"])
        selectbox_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            key = kwargs.get("key", "")
            selectbox_keys.append(key)
            # Return the first option so the function doesn't branch into button logic.
            return options[0] if options else None

        def noop_button(label, **kwargs):
            return False

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", side_effect=noop_button), \
             patch("app.ui.tabs.common.st.success"), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):

            # First call — simulates render_document_assembly_tab()
            select_or_create_case(service, key_prefix="assembly")
            # Second call — simulates render_document_ingestion_tab()
            select_or_create_case(service, key_prefix="ingestion")

        assert len(selectbox_keys) == 2, (
            f"Expected 2 selectbox calls, got {len(selectbox_keys)}"
        )
        key_first, key_second = selectbox_keys
        assert key_first != key_second, (
            f"Duplicate selectbox key detected: both calls used key={key_first!r}. "
            f"The fix should produce distinct keys 'assembly_case_select' and "
            f"'ingestion_case_select' for the two tabs."
        )
        assert key_first == "assembly_case_select", (
            f"Assembly tab selectbox key should be 'assembly_case_select', got {key_first!r}"
        )
        assert key_second == "ingestion_case_select", (
            f"Ingestion tab selectbox key should be 'ingestion_case_select', got {key_second!r}"
        )

    def test_button_keys_are_distinct_across_two_calls(self):
        """Two calls with distinct key_prefix values must register distinct button keys.

        Simulates the fixed calling convention: assembly tab passes
        key_prefix="assembly", ingestion tab passes key_prefix="ingestion".

        **Validates: Requirements 1.2, 2.1, 2.2, 2.3**
        """
        service = _make_service()  # empty list → choice == "(new case)" → button rendered
        button_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            # Return "(new case)" so the button branch is reached.
            return "(new case)"

        def capture_button(label, **kwargs):
            key = kwargs.get("key", "")
            button_keys.append(key)
            return False  # don't actually create a case

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", side_effect=capture_button), \
             patch("app.ui.tabs.common.st.success"), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):

            # First call — simulates render_document_assembly_tab()
            select_or_create_case(service, key_prefix="assembly")
            # Second call — simulates render_document_ingestion_tab()
            select_or_create_case(service, key_prefix="ingestion")

        assert len(button_keys) == 2, (
            f"Expected 2 button calls, got {len(button_keys)}"
        )
        key_first, key_second = button_keys
        assert key_first != key_second, (
            f"Duplicate button key detected: both calls used key={key_first!r}. "
            f"The fix should produce distinct keys 'assembly_new_case' and "
            f"'ingestion_new_case' for the two tabs."
        )
        assert key_first == "assembly_new_case", (
            f"Assembly tab button key should be 'assembly_new_case', got {key_first!r}"
        )
        assert key_second == "ingestion_new_case", (
            f"Ingestion tab button key should be 'ingestion_new_case', got {key_second!r}"
        )

    def test_all_four_widget_keys_are_unique_across_two_calls(self):
        """All four widget keys (2 selectboxes + 2 buttons) must be unique.

        Simulates the fixed calling convention: assembly tab passes
        key_prefix="assembly", ingestion tab passes key_prefix="ingestion".
        A single render pass with both tabs must not produce any duplicate key.

        **Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3**
        """
        service = _make_service()
        all_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            all_keys.append(kwargs.get("key", ""))
            return "(new case)"

        def capture_button(label, **kwargs):
            all_keys.append(kwargs.get("key", ""))
            return False

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", side_effect=capture_button), \
             patch("app.ui.tabs.common.st.success"), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):

            select_or_create_case(service, key_prefix="assembly")   # assembly tab
            select_or_create_case(service, key_prefix="ingestion")  # ingestion tab

        assert len(all_keys) == 4, (
            f"Expected 4 widget key registrations (2 selectboxes + 2 buttons), "
            f"got {len(all_keys)}: {all_keys}"
        )
        expected_keys = {
            "assembly_case_select", "assembly_new_case",
            "ingestion_case_select", "ingestion_new_case",
        }
        assert set(all_keys) == expected_keys, (
            f"Expected keys {expected_keys}, got {set(all_keys)}"
        )
        duplicates = [k for k in all_keys if all_keys.count(k) > 1]
        assert len(duplicates) == 0, (
            f"Duplicate widget keys found: {set(duplicates)!r}. "
            f"All registered keys were: {all_keys}. "
            f"The fix should produce 4 unique keys across the two tab render calls."
        )


# ---------------------------------------------------------------------------
# Property 2: Preservation — Existing Widget Behavior Unchanged
# ---------------------------------------------------------------------------
"""
Property 2: Preservation — Existing Widget Behavior Unchanged

These tests capture the baseline behavior of ``select_or_create_case(service)``
on UNFIXED code so that the same behavior can be verified after the fix.

**EXPECTED OUTCOME on UNFIXED code**: PASS — confirms the baseline to preserve.

**Validates: Requirements 3.1, 3.2, 3.3**
"""

class TestPreservationDefaultKeys:
    """Verify that calling with no key_prefix uses the default assembly_* keys.

    On UNFIXED code the keys are hardcoded to assembly_case_select /
    assembly_new_case, so these tests pass trivially and document the baseline.

    **Validates: Requirements 3.3**
    """

    def test_preservation_default_selectbox_key_is_assembly_case_select(self):
        """No key_prefix argument → selectbox key must be assembly_case_select.

        **Validates: Requirements 3.3**
        """
        service = _make_service(["case-001"])
        captured_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            captured_keys.append(kwargs.get("key", ""))
            return options[0] if options else None

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", return_value=False), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            select_or_create_case(service)

        assert len(captured_keys) == 1
        assert captured_keys[0] == "assembly_case_select", (
            f"Expected default selectbox key 'assembly_case_select', got {captured_keys[0]!r}"
        )

    def test_preservation_default_button_key_is_assembly_new_case(self):
        """No key_prefix argument → button key must be assembly_new_case.

        **Validates: Requirements 3.3**
        """
        service = _make_service()  # empty list → "(new case)" selected → button rendered
        captured_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            return "(new case)"

        def capture_button(label, **kwargs):
            captured_keys.append(kwargs.get("key", ""))
            return False

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", side_effect=capture_button), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            select_or_create_case(service)

        assert len(captured_keys) == 1
        assert captured_keys[0] == "assembly_new_case", (
            f"Expected default button key 'assembly_new_case', got {captured_keys[0]!r}"
        )


class TestPreservationReturnValue:
    """Verify the return value of select_or_create_case() for various case lists.

    These tests document the baseline return-value contract on UNFIXED code.
    They must pass before and after the fix.

    **Validates: Requirements 3.1, 3.2**
    """

    def test_preservation_returns_none_when_no_cases_and_no_button_press(self):
        """Empty case list + no button press → return value is None.

        **Validates: Requirements 3.1**
        """
        service = _make_service()

        def capture_selectbox(label, options, **kwargs):
            return "(new case)"

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", return_value=False), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            result = select_or_create_case(service)

        assert result is None, (
            f"Expected None when no cases and button not pressed, got {result!r}"
        )

    def test_preservation_returns_selected_case_id_when_case_chosen(self):
        """Non-empty case list + user selects a case → return value is that case ID.

        **Validates: Requirements 3.1**
        """
        service = _make_service(["case-abc", "case-def"])

        def capture_selectbox(label, options, **kwargs):
            # Simulate user selecting the first real case (index 1, after "(new case)")
            return "case-abc"

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", return_value=False), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            result = select_or_create_case(service)

        assert result == "case-abc", (
            f"Expected selected case ID 'case-abc', got {result!r}"
        )

    @pytest.mark.parametrize("case_ids", [
        ["case-001"],
        ["case-001", "case-002"],
        ["case-001", "case-002", "case-003"],
        ["alpha", "beta", "gamma", "delta"],
    ])
    def test_preservation_returns_selected_case_id_for_varied_case_lists(self, case_ids):
        """For any non-empty case list, selecting a case returns its ID.

        Parametrized over varied case lists to confirm the return-value contract
        holds regardless of list length.

        **Validates: Requirements 3.1, 3.2**
        """
        service = _make_service(case_ids)
        expected_id = case_ids[0]

        def capture_selectbox(label, options, **kwargs):
            return expected_id

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", return_value=False), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            result = select_or_create_case(service)

        assert result == expected_id, (
            f"Expected {expected_id!r}, got {result!r} for case list {case_ids}"
        )

    @pytest.mark.parametrize("persisted_id", [None, "existing-case-id"])
    def test_preservation_returns_persisted_id_when_new_case_not_created(self, persisted_id):
        """(new case) selected + button not pressed → returns persisted_case_id from session.

        **Validates: Requirements 3.1**
        """
        service = _make_service()

        def capture_selectbox(label, options, **kwargs):
            return "(new case)"

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", return_value=False), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=persisted_id), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            result = select_or_create_case(service)

        assert result == persisted_id, (
            f"Expected persisted_id={persisted_id!r}, got {result!r}"
        )


class TestPreservationKeyPrefixPattern:
    """Verify that key_prefix controls the widget key names.

    These tests are marked xfail on UNFIXED code because the function signature
    does not yet accept a key_prefix argument. They will pass after the fix.

    **Validates: Requirements 2.2, 2.3, 3.3**
    """

    @pytest.mark.parametrize("prefix,expected_select_key,expected_button_key", [
        ("assembly", "assembly_case_select", "assembly_new_case"),
        ("ingestion", "ingestion_case_select", "ingestion_new_case"),
        ("custom", "custom_case_select", "custom_new_case"),
    ])
    def test_preservation_key_prefix_controls_widget_keys(
        self, prefix, expected_select_key, expected_button_key
    ):
        """key_prefix=X → selectbox key is X_case_select, button key is X_new_case.

        **Validates: Requirements 2.2, 2.3**
        """
        service = _make_service()
        captured_select_keys: list[str] = []
        captured_button_keys: list[str] = []

        def capture_selectbox(label, options, **kwargs):
            captured_select_keys.append(kwargs.get("key", ""))
            return "(new case)"

        def capture_button(label, **kwargs):
            captured_button_keys.append(kwargs.get("key", ""))
            return False

        with patch("app.ui.tabs.common.st.selectbox", side_effect=capture_selectbox), \
             patch("app.ui.tabs.common.st.button", side_effect=capture_button), \
             patch("app.ui.tabs.common.session.get_persisted_case_id", return_value=None), \
             patch("app.ui.tabs.common.session.set_persisted_case_id"):
            select_or_create_case(service, key_prefix=prefix)

        assert captured_select_keys == [expected_select_key], (
            f"key_prefix={prefix!r}: expected selectbox key {expected_select_key!r}, "
            f"got {captured_select_keys}"
        )
        assert captured_button_keys == [expected_button_key], (
            f"key_prefix={prefix!r}: expected button key {expected_button_key!r}, "
            f"got {captured_button_keys}"
        )

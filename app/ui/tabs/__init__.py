"""Streamlit tab modules (Milestone 12: UI modularization).

``app/ui/case_ui.py`` used to be a single ~1000-line module holding every
``render_*`` tab function plus shared helpers. Milestone 12 split it into
cohesive modules here, grouped by domain:

- :mod:`common`               - shared service access + persistence bridge
- :mod:`case_tabs`            - case management, human review, audit, metrics
- :mod:`ingestion_tabs`       - document ingestion + OCR status
- :mod:`assembly_tabs`        - document assembly, evidence explorer, conflicts
- :mod:`evidence_quality_tabs`- evidence quality + reviewer workbench
- :mod:`resolution_tabs`      - conflict resolution + reviewer feedback
- :mod:`governance_tabs`      - governance settings + quality analytics

Behavior is preserved exactly; ``app/ui/case_ui.py`` re-exports these names so
``dashboard.py`` (and anything else) keeps working unchanged.
"""

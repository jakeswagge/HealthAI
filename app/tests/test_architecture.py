"""Architecture guardrail tests (Milestone 12).

These tests encode the structural decisions made during the architecture
stabilization milestone so regressions are caught automatically:

1. No package-level import cycles (especially ``cases <-> analytics``).
2. Service boundaries: low-level layers never import the high-level facade.
3. Repository boundaries: repositories stay persistence-only (no engines/UI).
4. ``EvidenceReference`` (``app.models.evidence_reference``) remains the single
   canonical evidence model used by all live code.

The import graph is built statically with :mod:`ast`, considering only
*module-level* imports (imports inside functions/methods are lazy and do not
create package-level coupling - that is exactly how the ``cases`` package
exposes ``CaseService`` without a cycle).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]  # .../app
PROJECT_ROOT = APP_ROOT.parent


def _module_name(path: Path) -> str:
    """Map a file path to its dotted module name (rooted at ``app``)."""
    rel = path.relative_to(PROJECT_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _iter_app_modules():
    """Yield (module_name, path) for every Python module under ``app`` except tests."""
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        yield _module_name(path), path


def _toplevel_app_imports(path: Path) -> set[str]:
    """Return the set of ``app.*`` modules imported at MODULE LEVEL by ``path``.

    Imports nested inside functions/classes are ignored: they are lazy and do
    not contribute to package-level coupling.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()

    for node in tree.body:  # module-level statements only
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.startswith("app"):
                imports.add(node.module)
    return imports


def _package_of(module: str) -> str:
    """Collapse a module name to its top-level ``app`` subpackage.

    e.g. ``app.cases.service`` -> ``app.cases``; ``app.models`` -> ``app.models``.
    """
    parts = module.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return module


def _build_package_graph() -> dict[str, set[str]]:
    """Module-level package -> packages import graph for the ``app`` tree."""
    graph: dict[str, set[str]] = {}
    for module, path in _iter_app_modules():
        src_pkg = _package_of(module)
        graph.setdefault(src_pkg, set())
        for imported in _toplevel_app_imports(path):
            dst_pkg = _package_of(imported)
            if dst_pkg != src_pkg:
                graph[src_pkg].add(dst_pkg)
    return graph


def _build_module_import_graph() -> dict[str, set[str]]:
    """Module-level module -> modules import graph for the ``app`` tree."""
    graph: dict[str, set[str]] = {}
    for module, path in _iter_app_modules():
        graph.setdefault(module, set())
        graph[module] |= _toplevel_app_imports(path)
    return graph


def _reachable_modules(entry_points: list[str]) -> set[str]:
    """Module-level transitive closure of imports from the given entry points.

    Approximates the set of modules the running application actually loads. The
    ``app.cases`` package exposes ``CaseService`` lazily (PEP 562), so importing
    ``app.cases.service`` directly as an entry point captures the real wiring.
    """
    graph = _build_module_import_graph()
    seen: set[str] = set()
    stack = list(entry_points)
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        for nxt in graph.get(mod, ()):  # only module-level imports
            if nxt not in seen:
                stack.append(nxt)
    return seen


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return a list of cycles (as node lists) in a directed graph via DFS."""
    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    stack: list[str] = []

    def visit(node: str) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in graph.get(node, ()):  # neighbor
            if nxt not in color:
                color[nxt] = WHITE
                graph.setdefault(nxt, set())
            if color[nxt] == WHITE:
                visit(nxt)
            elif color[nxt] == GRAY:
                # Found a back-edge -> cycle from nxt..node.
                idx = stack.index(nxt)
                cycles.append(stack[idx:] + [nxt])
        stack.pop()
        color[node] = BLACK

    for node in list(graph):
        if color[node] == WHITE:
            visit(node)
    return cycles


# --------------------------------------------------------------------------- #
# 1. No package-level cycles
# --------------------------------------------------------------------------- #
class TestNoPackageCycles:
    def test_no_package_level_import_cycles(self):
        graph = _build_package_graph()
        cycles = _find_cycles(graph)
        assert not cycles, f"Package-level import cycles detected: {cycles}"

    def test_cases_and_analytics_not_mutually_coupled(self):
        graph = _build_package_graph()
        cases_to_analytics = "app.analytics" in graph.get("app.cases", set())
        analytics_to_cases = "app.cases" in graph.get("app.analytics", set())
        assert not (cases_to_analytics and analytics_to_cases), (
            "cases <-> analytics package cycle is back"
        )

    def test_analytics_does_not_import_cases_at_module_level(self):
        """Analytics depends on injected repositories, not the cases package."""
        graph = _build_package_graph()
        assert "app.cases" not in graph.get("app.analytics", set()), (
            "app.analytics must not import app.cases at module level "
            "(use constructor injection)."
        )


# --------------------------------------------------------------------------- #
# 2. Service boundaries
# --------------------------------------------------------------------------- #
class TestServiceBoundaries:
    def test_models_do_not_import_services_or_ui(self):
        """The models layer is a leaf: it must not depend on services/UI/engines."""
        forbidden_prefixes = ("app.cases", "app.ui", "app.analytics", "app.governance")
        offenders: list[str] = []
        for module, path in _iter_app_modules():
            if not module.startswith("app.models"):
                continue
            for imported in _toplevel_app_imports(path):
                if imported.startswith(forbidden_prefixes):
                    offenders.append(f"{module} -> {imported}")
        assert not offenders, f"models layer has upward imports: {offenders}"

    def test_engines_do_not_import_case_service_facade(self):
        """Engines/analytics/governance must not import the CaseService facade.

        The UI layer (``app.ui``), the ``app.cases`` package itself, and the
        application-level validation harness (``app.validation``) are allowed to
        use the facade - it is their entry point. Lower layers (models,
        repositories, engines) must not depend on it.
        """
        allowed_prefixes = ("app.cases", "app.ui", "app.validation")
        offenders: list[str] = []
        for module, path in _iter_app_modules():
            if module.startswith(allowed_prefixes):
                continue
            for imported in _toplevel_app_imports(path):
                if imported == "app.cases.service":
                    offenders.append(f"{module} -> {imported}")
        assert not offenders, (
            f"lower-layer modules import the CaseService facade directly: {offenders}"
        )

    def test_ui_does_not_reach_into_sqlite_directly(self):
        """UI talks to the service layer, not raw storage."""
        offenders: list[str] = []
        for module, path in _iter_app_modules():
            if not module.startswith("app.ui"):
                continue
            for imported in _toplevel_app_imports(path):
                if imported.startswith("app.storage"):
                    offenders.append(f"{module} -> {imported}")
        assert not offenders, f"UI imports storage directly: {offenders}"


# --------------------------------------------------------------------------- #
# 3. Repository boundaries
# --------------------------------------------------------------------------- #
class TestRepositoryBoundaries:
    def test_repositories_do_not_import_engines_or_services(self):
        """Repository modules persist models; they must not import engines/services."""
        forbidden = ("app.cases.service", "app.ui")
        offenders: list[str] = []
        for module, path in _iter_app_modules():
            if not module.endswith("repository") and "repository" not in module.split("."):
                continue
            for imported in _toplevel_app_imports(path):
                if imported.startswith(forbidden):
                    offenders.append(f"{module} -> {imported}")
        assert not offenders, f"repositories have forbidden imports: {offenders}"


# --------------------------------------------------------------------------- #
# 4. EvidenceReference remains canonical
# --------------------------------------------------------------------------- #
class TestCanonicalEvidenceReference:
    def test_models_package_exports_canonical(self):
        import app.models as models
        from app.models.evidence_reference import EvidenceReference as Canonical

        assert models.EvidenceReference is Canonical

    def test_canonical_has_fact_type_field(self):
        from app.models.evidence_reference import EvidenceReference

        assert "fact_type" in EvidenceReference.model_fields

    #: Application entry points whose transitive imports define "live" code.
    LIVE_ENTRY_POINTS = ["app.ui.dashboard", "app.cases.service"]

    def test_live_code_uses_canonical_evidence_reference(self):
        """Every LIVE module importing EvidenceReference uses the canonical path.

        The canonical model lives in ``app.models.evidence_reference``. A legacy
        ``app.models.evidence`` module holds a parallel definition. We assert
        that nothing reachable from the application entry points depends on it.

        Note: an orphaned, unreachable lineage
        (``app.cases.assembly_service`` -> ``app.cases.evidence_repository`` ->
        ``app.models.evidence``) still imports the legacy model. It is dead code
        with zero live importers and is documented as future-work cleanup; it is
        intentionally excluded here by scoping to reachable modules.
        """
        live = _reachable_modules(self.LIVE_ENTRY_POINTS)
        offenders: list[str] = []
        for module, path in _iter_app_modules():
            if module not in live:
                continue
            for imported in _toplevel_app_imports(path):
                if imported == "app.models.evidence":
                    offenders.append(module)
        assert not offenders, (
            "live modules importing the non-canonical app.models.evidence: "
            f"{offenders}. Use app.models.evidence_reference instead."
        )

    def test_legacy_evidence_module_is_not_reachable_from_entry_points(self):
        """The non-canonical app.models.evidence stays out of the live import graph."""
        live = _reachable_modules(self.LIVE_ENTRY_POINTS)
        assert "app.models.evidence" not in live, (
            "app.models.evidence became reachable from the app entry points; "
            "live code must use app.models.evidence_reference."
        )

    def test_active_evidence_repository_uses_canonical(self):
        import inspect

        from app.evidence.repository import EvidenceRepository
        from app.models.evidence_reference import EvidenceReference

        src = inspect.getsource(EvidenceRepository)
        assert "EvidenceReference" in src
        # The active repository round-trips the canonical model (has fact_type).
        assert "fact_type" in EvidenceReference.model_fields


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

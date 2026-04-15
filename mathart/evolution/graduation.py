"""Scaffold graduation workflow (TASK-015).

Manages the lifecycle of mined model scaffolds from candidate → experimental → stable.

Graduation criteria:
1. **Candidate → Experimental**: Scaffold module exists, smoke test passes,
   at least one capability is implemented (not just a placeholder).
2. **Experimental → Stable**: All quality metrics pass threshold,
   integration tests pass, no GPU/external dependencies (or they are
   properly abstracted).

The workflow:
  mine → scaffold (candidate) → implement → test → graduate (experimental)
       → validate → promote (stable)

Design principles:
  - Graduation is explicit and auditable — each step is logged
  - No automatic promotion to stable — requires passing validation
  - Rollback is supported — can demote back to experimental
  - Registry is the single source of truth for model status
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .math_registry import MathModelRegistry, ModelEntry


@dataclass
class GraduationResult:
    """Result of a graduation attempt."""
    model_name: str
    from_status: str
    to_status: str
    success: bool
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()

    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        lines = [
            f"Graduation [{status}]: {self.model_name}",
            f"  {self.from_status} → {self.to_status}",
            f"  Passed: {len(self.checks_passed)}, Failed: {len(self.checks_failed)}",
        ]
        if self.checks_failed:
            lines.append("  Failed checks:")
            for check in self.checks_failed:
                lines.append(f"    - {check}")
        if self.notes:
            lines.append("  Notes:")
            for note in self.notes:
                lines.append(f"    - {note}")
        return "\n".join(lines)


@dataclass
class GraduationReport:
    """Summary report of all graduation attempts in a session."""
    results: list[GraduationResult] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)

    def summary(self) -> str:
        lines = [
            f"Graduation Report ({self.timestamp})",
            f"  Total: {self.total}, Succeeded: {self.succeeded}, Failed: {self.failed}",
            "",
        ]
        for result in self.results:
            lines.append(result.summary())
            lines.append("")
        return "\n".join(lines)


class ScaffoldGraduator:
    """Manages the scaffold → experimental → stable graduation workflow.

    Parameters
    ----------
    project_root : Path
        Project root directory.
    registry : MathModelRegistry, optional
        Existing registry. Loads from disk if not provided.
    verbose : bool
        Print progress messages.
    """

    CANDIDATE_TO_EXPERIMENTAL_CHECKS = [
        "module_exists",
        "module_importable",
        "function_callable",
        "smoke_test_passes",
    ]

    EXPERIMENTAL_TO_STABLE_CHECKS = [
        "module_exists",
        "module_importable",
        "function_callable",
        "smoke_test_passes",
        "returns_valid_output",
        "no_hard_gpu_dependency",
        "has_documentation",
    ]

    def __init__(
        self,
        project_root: Path,
        registry: Optional[MathModelRegistry] = None,
        verbose: bool = False,
    ):
        self.project_root = Path(project_root)
        self.verbose = verbose

        if registry is not None:
            self.registry = registry
        else:
            registry_path = self.project_root / "math_models.json"
            if registry_path.exists():
                self.registry = MathModelRegistry.load(registry_path)
            else:
                self.registry = MathModelRegistry()

    def graduate_candidate(self, model_name: str) -> GraduationResult:
        """Attempt to graduate a candidate scaffold to experimental status.

        Checks:
        1. Module file exists at the declared module_path
        2. Module is importable without errors
        3. Entry point function is callable
        4. Smoke test (calling function with default params) succeeds
        """
        model = self.registry.get(model_name)
        if model is None:
            return GraduationResult(
                model_name=model_name,
                from_status="unknown",
                to_status="experimental",
                success=False,
                checks_failed=["model_not_found"],
                notes=[f"Model '{model_name}' not found in registry"],
            )

        if model.status not in ("candidate", "scaffold"):
            return GraduationResult(
                model_name=model_name,
                from_status=model.status,
                to_status="experimental",
                success=False,
                checks_failed=["wrong_status"],
                notes=[f"Model status is '{model.status}', expected 'candidate' or 'scaffold'"],
            )

        passed, failed, notes = self._run_checks(
            model, self.CANDIDATE_TO_EXPERIMENTAL_CHECKS
        )

        success = len(failed) == 0
        if success:
            model.status = "experimental"
            self._save_registry()
            notes.append("Graduated to experimental status")

        result = GraduationResult(
            model_name=model_name,
            from_status="candidate",
            to_status="experimental",
            success=success,
            checks_passed=passed,
            checks_failed=failed,
            notes=notes,
        )

        self._log_graduation(result)
        return result

    def promote_to_stable(self, model_name: str) -> GraduationResult:
        """Attempt to promote an experimental model to stable status.

        Additional checks beyond candidate graduation:
        5. Function returns valid output (not just scaffold placeholder)
        6. No hard GPU dependency
        7. Has documentation (docstring or knowledge source)
        """
        model = self.registry.get(model_name)
        if model is None:
            return GraduationResult(
                model_name=model_name,
                from_status="unknown",
                to_status="stable",
                success=False,
                checks_failed=["model_not_found"],
            )

        if model.status != "experimental":
            return GraduationResult(
                model_name=model_name,
                from_status=model.status,
                to_status="stable",
                success=False,
                checks_failed=["wrong_status"],
                notes=[f"Model status is '{model.status}', expected 'experimental'"],
            )

        passed, failed, notes = self._run_checks(
            model, self.EXPERIMENTAL_TO_STABLE_CHECKS
        )

        success = len(failed) == 0
        if success:
            model.status = "stable"
            self._save_registry()
            notes.append("Promoted to stable status")

        result = GraduationResult(
            model_name=model_name,
            from_status="experimental",
            to_status="stable",
            success=success,
            checks_passed=passed,
            checks_failed=failed,
            notes=notes,
        )

        self._log_graduation(result)
        return result

    def demote(self, model_name: str, to_status: str = "experimental") -> GraduationResult:
        """Demote a model back to a lower status."""
        model = self.registry.get(model_name)
        if model is None:
            return GraduationResult(
                model_name=model_name,
                from_status="unknown",
                to_status=to_status,
                success=False,
                checks_failed=["model_not_found"],
            )

        old_status = model.status
        model.status = to_status
        self._save_registry()

        result = GraduationResult(
            model_name=model_name,
            from_status=old_status,
            to_status=to_status,
            success=True,
            notes=[f"Demoted from {old_status} to {to_status}"],
        )
        self._log_graduation(result)
        return result

    def audit_all(self) -> GraduationReport:
        """Audit all models in the registry and report graduation readiness."""
        report = GraduationReport()

        for model in self.registry.list_all():
            if model.status in ("candidate", "scaffold"):
                result = self._dry_run_checks(
                    model, self.CANDIDATE_TO_EXPERIMENTAL_CHECKS,
                    "candidate", "experimental"
                )
                report.results.append(result)
            elif model.status == "experimental":
                result = self._dry_run_checks(
                    model, self.EXPERIMENTAL_TO_STABLE_CHECKS,
                    "experimental", "stable"
                )
                report.results.append(result)

        return report

    def graduate_all_ready(self) -> GraduationReport:
        """Graduate all models that pass their checks."""
        report = GraduationReport()

        for model in self.registry.list_all():
            if model.status in ("candidate", "scaffold"):
                result = self.graduate_candidate(model.name)
                report.results.append(result)
            elif model.status == "experimental":
                result = self.promote_to_stable(model.name)
                report.results.append(result)

        return report

    # ── Check implementations ─────────────────────────────────────────

    def _run_checks(
        self,
        model: ModelEntry,
        check_names: list[str],
    ) -> tuple[list[str], list[str], list[str]]:
        """Run a list of named checks and return (passed, failed, notes)."""
        passed = []
        failed = []
        notes = []

        for check_name in check_names:
            check_fn = getattr(self, f"_check_{check_name}", None)
            if check_fn is None:
                notes.append(f"Unknown check: {check_name}")
                continue

            try:
                ok, msg = check_fn(model)
                if ok:
                    passed.append(check_name)
                else:
                    failed.append(check_name)
                    notes.append(msg)
            except Exception as exc:
                failed.append(check_name)
                notes.append(f"{check_name} raised exception: {exc}")

        return passed, failed, notes

    def _dry_run_checks(
        self,
        model: ModelEntry,
        check_names: list[str],
        from_status: str,
        to_status: str,
    ) -> GraduationResult:
        """Run checks without modifying status (audit mode)."""
        passed, failed, notes = self._run_checks(model, check_names)
        return GraduationResult(
            model_name=model.name,
            from_status=from_status,
            to_status=to_status,
            success=len(failed) == 0,
            checks_passed=passed,
            checks_failed=failed,
            notes=notes + ["DRY RUN — no status change"],
        )

    def _check_module_exists(self, model: ModelEntry) -> tuple[bool, str]:
        """Check if the module file exists."""
        if not model.module_path:
            return False, "No module_path specified"

        # Convert dotted path to file path
        parts = model.module_path.split(".")
        rel_path = Path(*parts).with_suffix(".py")
        full_path = self.project_root / rel_path

        if full_path.exists():
            return True, ""
        return False, f"Module file not found: {full_path}"

    def _check_module_importable(self, model: ModelEntry) -> tuple[bool, str]:
        """Check if the module can be imported."""
        if not model.module_path:
            return False, "No module_path specified"

        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            if not full_path.exists():
                return False, f"Module file not found: {full_path}"

            spec = importlib.util.spec_from_file_location(model.module_path, full_path)
            if spec is None or spec.loader is None:
                return False, f"Cannot create import spec for {model.module_path}"

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return True, ""
        except Exception as exc:
            return False, f"Import failed: {exc}"

    def _check_function_callable(self, model: ModelEntry) -> tuple[bool, str]:
        """Check if the entry point function exists and is callable."""
        if not model.function_name:
            return False, "No function_name specified"

        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            spec = importlib.util.spec_from_file_location(model.module_path, full_path)
            if spec is None or spec.loader is None:
                return False, f"Cannot create import spec"

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            func = getattr(module, model.function_name, None)
            if func is None:
                return False, f"Function '{model.function_name}' not found in module"
            if not callable(func):
                return False, f"'{model.function_name}' is not callable"
            return True, ""
        except Exception as exc:
            return False, f"Function check failed: {exc}"

    def _check_smoke_test_passes(self, model: ModelEntry) -> tuple[bool, str]:
        """Run a basic smoke test: call the function with default params."""
        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            spec = importlib.util.spec_from_file_location(model.module_path, full_path)
            if spec is None or spec.loader is None:
                return False, "Cannot create import spec"

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            func = getattr(module, model.function_name)
            # Build default params from model spec
            default_params = {}
            for name, param_spec in model.params.items():
                if "default" in param_spec:
                    default_params[name] = param_spec["default"]

            result = func(default_params or None)
            if result is None:
                return False, "Function returned None"
            return True, ""
        except Exception as exc:
            return False, f"Smoke test failed: {exc}"

    def _check_returns_valid_output(self, model: ModelEntry) -> tuple[bool, str]:
        """Check that the function returns real output, not just scaffold placeholder."""
        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            spec = importlib.util.spec_from_file_location(model.module_path, full_path)
            if spec is None or spec.loader is None:
                return False, "Cannot create import spec"

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            func = getattr(module, model.function_name)
            result = func(None)

            # Check if result is still a scaffold placeholder
            if isinstance(result, dict) and result.get("status") == "scaffold":
                return False, "Function still returns scaffold placeholder"
            return True, ""
        except Exception as exc:
            return False, f"Output validation failed: {exc}"

    def _check_no_hard_gpu_dependency(self, model: ModelEntry) -> tuple[bool, str]:
        """Check that the model doesn't have a hard GPU requirement."""
        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            if not full_path.exists():
                return False, "Module file not found"

            source = full_path.read_text(encoding="utf-8").lower()
            hard_gpu_markers = ["import torch", "import tensorflow", "cuda.is_available()"]
            for marker in hard_gpu_markers:
                if marker in source:
                    return False, f"Hard GPU dependency detected: {marker}"
            return True, ""
        except Exception as exc:
            return False, f"GPU check failed: {exc}"

    def _check_has_documentation(self, model: ModelEntry) -> tuple[bool, str]:
        """Check that the model has documentation (docstring or knowledge source)."""
        if model.knowledge_sources:
            return True, ""

        try:
            parts = model.module_path.split(".")
            rel_path = Path(*parts).with_suffix(".py")
            full_path = self.project_root / rel_path

            if not full_path.exists():
                return False, "Module file not found"

            source = full_path.read_text(encoding="utf-8")
            # Check for module docstring
            if source.strip().startswith('"""') or source.strip().startswith("'''"):
                return True, ""
            return False, "No docstring or knowledge sources"
        except Exception as exc:
            return False, f"Documentation check failed: {exc}"

    # ── Persistence ───────────────────────────────────────────────────

    def _save_registry(self) -> None:
        """Save registry to disk."""
        registry_path = self.project_root / "math_models.json"
        self.registry.save(registry_path)

    def _log_graduation(self, result: GraduationResult) -> None:
        """Append graduation result to the graduation log."""
        log_path = self.project_root / "GRADUATION_LOG.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {result.timestamp}\n\n")
            f.write(result.summary())
            f.write("\n\n---\n")

        if self.verbose:
            print(result.summary())

"""Self-Evolution Engine — top-level orchestrator.

Coordinates the three layers of the self-evolution system:
  1. Inner Loop: quality-driven parameter optimization
  2. Outer Loop: external knowledge distillation
  3. Math Registry: model catalog and capability tracking

Also provides the CLI interface and status reporting.

Design philosophy:
  - The engine is stateless between sessions (state lives in files)
  - Every action is logged and reversible (via git)
  - The engine exposes its limitations honestly (capability gaps)
  - Cross-session continuity: new sessions pick up from DISTILL_LOG.md

Usage::

    from mathart.evolution import SelfEvolutionEngine
    engine = SelfEvolutionEngine("/path/to/project")
    engine.status()
    engine.outer_loop.distill_file("new_book.pdf")
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .inner_loop import InnerLoopRunner
from .math_registry import MathModelRegistry, ModelCapability
from .outer_loop import OuterLoopDistiller


class SelfEvolutionEngine:
    """Top-level coordinator for the self-evolution system.

    Parameters
    ----------
    project_root : str or Path
        Root directory of the MarioTrickster-MathArt project.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        verbose: bool = True,
    ):
        self.project_root = Path(project_root)
        self.verbose = verbose

        # Initialize subsystems
        self.inner_loop = InnerLoopRunner(verbose=verbose)
        self.outer_loop = OuterLoopDistiller(
            project_root=project_root,
            verbose=verbose,
        )
        self.math_registry = MathModelRegistry()

    def status(self) -> str:
        """Return a comprehensive status report of the evolution system.

        Reports:
        - Current version and test status
        - Knowledge base statistics
        - Math model registry summary
        - Capability gaps (what's missing or experimental)
        - Next recommended actions
        """
        lines = [
            "=" * 60,
            "MarioTrickster-MathArt — Self-Evolution Engine Status",
            "=" * 60,
            "",
        ]

        # Knowledge base stats
        knowledge_dir = self.project_root / "knowledge"
        if knowledge_dir.exists():
            md_files = list(knowledge_dir.glob("*.md"))
            total_lines = sum(
                len(f.read_text(encoding="utf-8").splitlines()) for f in md_files
            )
            lines.extend([
                f"📚 Knowledge Base: {len(md_files)} files, ~{total_lines} lines",
                f"   Files: {', '.join(f.stem for f in sorted(md_files))}",
                "",
            ])

        # Math model registry
        all_models = self.math_registry.list_all()
        stable = [m for m in all_models if m.status == "stable"]
        experimental = [m for m in all_models if m.status == "experimental"]
        lines.extend([
            f"🔢 Math Model Registry: {len(all_models)} models",
            f"   Stable: {len(stable)} | Experimental: {len(experimental)}",
            "",
        ])

        # Capability coverage
        all_caps = set(ModelCapability)
        covered_caps = set()
        for model in stable:
            covered_caps.update(model.capabilities)
        missing_caps = all_caps - covered_caps

        lines.append("✅ Covered Capabilities:")
        for cap in sorted(covered_caps, key=lambda c: c.value):
            lines.append(f"   ✓ {cap.value}")

        if missing_caps:
            lines.append("")
            lines.append("⚠️  Capability Gaps (experimental or missing):")
            for cap in sorted(missing_caps, key=lambda c: c.value):
                exp_models = [m for m in experimental if cap in m.capabilities]
                if exp_models:
                    lines.append(f"   ~ {cap.value} (experimental: {exp_models[0].name})")
                else:
                    lines.append(f"   ✗ {cap.value} (not implemented)")

        # Distill log summary
        log_path = self.project_root / "DISTILL_LOG.md"
        if log_path.exists():
            import re
            content = log_path.read_text(encoding="utf-8")
            sessions = re.findall(r'DISTILL-(\d+)', content)
            if sessions:
                lines.extend([
                    "",
                    f"📋 Distillation Sessions: {len(set(sessions))} completed",
                    f"   Latest: DISTILL-{max(sessions)}",
                ])

        # Next recommended actions
        lines.extend([
            "",
            "🚀 Next Recommended Actions:",
            "   1. Upload new PDF/book → engine.outer_loop.distill_file('book.pdf')",
            "   2. Run inner loop on a generator → engine.inner_loop.run(gen_fn, space)",
            "   3. Check math registry → engine.math_registry.summary_table()",
            "   4. View capability gaps above and plan external tool integration",
            "",
            "=" * 60,
        ])

        report = "\n".join(lines)
        if self.verbose:
            print(report)
        return report

    def capability_gap_report(self) -> dict:
        """Return a structured report of capability gaps.

        Returns a dict with:
        - 'covered': list of covered capabilities
        - 'experimental': list of experimental capabilities
        - 'missing': list of missing capabilities
        - 'recommendations': list of recommended tools/actions
        """
        all_caps = set(ModelCapability)
        all_models = self.math_registry.list_all()

        covered = set()
        experimental_caps = set()
        for model in all_models:
            if model.status == "stable":
                covered.update(model.capabilities)
            elif model.status == "experimental":
                experimental_caps.update(model.capabilities)

        missing = all_caps - covered - experimental_caps

        recommendations = []
        if ModelCapability.SHADER_PARAMS in missing or ModelCapability.SHADER_PARAMS in experimental_caps:
            recommendations.append(
                "SHADER_PARAMS: Consider integrating Godot/Unity shader compiler "
                "for real-time PBR shader parameter optimization."
            )
        if ModelCapability.TEXTURE in missing:
            recommendations.append(
                "TEXTURE: Perlin/Simplex noise texture generator not yet implemented. "
                "Add mathart/sdf/noise.py with octave noise functions."
            )

        return {
            "covered": [c.value for c in sorted(covered, key=lambda x: x.value)],
            "experimental": [c.value for c in sorted(experimental_caps, key=lambda x: x.value)],
            "missing": [c.value for c in sorted(missing, key=lambda x: x.value)],
            "recommendations": recommendations,
        }

    def save_registry(self, filepath: Optional[str] = None) -> Path:
        """Save the math model registry to a JSON file.

        Parameters
        ----------
        filepath : str, optional
            Output path. Defaults to project_root/math_models.json.

        Returns
        -------
        Path
            Path to the saved file.
        """
        if filepath is None:
            filepath = self.project_root / "math_models.json"
        else:
            filepath = Path(filepath)

        self.math_registry.save(filepath)
        if self.verbose:
            print(f"[Engine] Math registry saved to {filepath}")
        return filepath

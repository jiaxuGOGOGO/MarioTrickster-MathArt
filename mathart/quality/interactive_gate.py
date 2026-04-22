"""Interactive Preview Gate — Multi-Round REPL & Blueprint Sedimentation.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2

This module implements the **Interactive Animatic REPL** — a mandatory
checkpoint that fires *before* any heavy AI/GPU rendering is invoked.

Workflow:
1. Receive a ``CreatorIntentSpec`` with a resolved ``Genotype``.
2. Generate a sub-second **wireframe proxy** (matplotlib/PIL, zero GPU).
3. Enter a terminal REPL loop:
   - ``[1]`` Approve → proceed to render (and optionally save Blueprint).
   - ``[2]`` ``[+]`` More exaggerated → amplify parameters, regenerate proxy.
   - ``[3]`` ``[-]`` More conservative → dampen parameters, regenerate proxy.
   - ``[4]`` Abort → exit without rendering.
4. On approval, offer to **save the converged genotype as a Blueprint**.

Architecture discipline:
- This module is an **independent quality gate** mounted under
  ``mathart/quality/``.  It NEVER directly invokes ComfyUI or any GPU
  backend.
- The ONLY way heavy rendering proceeds is when this gate returns
  ``GateDecision.APPROVED``.

Red-line enforcement:
- **No blind GPU render**: ComfyUI daemon is NEVER awakened unless the user
  explicitly selects ``[1]``.
- **Blueprint purity**: Saved YAML contains no Base64, no absolute paths,
  no runtime state.
- **Backward compat**: Loading old blueprints with missing keys uses robust
  defaults (via ``Genotype.from_dict``).

External research anchors (SESSION-139):
- DreamCrafter (ACM 2025): proxy-preview iterative editing
- Pixar animatic pipeline: low-fidelity preview before commit
- Interactive Genetic Algorithm with proxy model (Electronics 2024)
"""
from __future__ import annotations

import copy
import io
import logging
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gate decision enum
# ---------------------------------------------------------------------------

class GateDecision(str, Enum):
    """Outcome of the interactive preview gate."""
    APPROVED = "approved"          # User confirmed → proceed to render
    ABORTED = "aborted"            # User cancelled
    BLUEPRINT_SAVED = "blueprint_saved"  # Approved AND blueprint was saved


# ---------------------------------------------------------------------------
# Proxy renderer (zero-GPU, sub-second)
# ---------------------------------------------------------------------------

class ProxyRenderer:
    """Generates a low-fidelity wireframe preview from a Genotype.

    This is intentionally simple — matplotlib scatter/line art that
    visualizes the *parameter space* rather than the final asset.
    The goal is to give the artist a quick spatial intuition before
    committing to expensive rendering.
    """

    @staticmethod
    def render_proxy(genotype: "Genotype", output_path: Optional[Path] = None) -> Path:
        """Render a wireframe proxy GIF/PNG from the genotype.

        Returns the path to the generated image file.
        """
        from ..workspace.director_intent import Genotype
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        physics = genotype.physics
        anim = genotype.animation
        proportions = genotype.proportions

        fig, axes = plt.subplots(1, 3, figsize=(12, 4))

        # Panel 1: Physics trajectory preview
        ax1 = axes[0]
        t = np.linspace(0, 2.0, 200)
        # Simple spring-damper trajectory
        omega = np.sqrt(max(physics.stiffness / max(physics.mass, 0.01), 0.1))
        zeta = physics.damping / (2.0 * np.sqrt(max(physics.stiffness * physics.mass, 0.01)))
        zeta = min(zeta, 0.99)
        y = physics.bounce * np.exp(-zeta * omega * t) * np.cos(omega * np.sqrt(1 - zeta**2) * t)
        ax1.plot(t, y, "b-", linewidth=2)
        ax1.set_title(f"Physics Trajectory\nmass={physics.mass:.1f} stiff={physics.stiffness:.0f}")
        ax1.set_xlabel("Time (s)")
        ax1.set_ylabel("Displacement")
        ax1.axhline(y=0, color="gray", linestyle="--", alpha=0.5)
        ax1.grid(True, alpha=0.3)

        # Panel 2: Proportions wireframe
        ax2 = axes[1]
        head_h = proportions.head_ratio * 100
        body_h = proportions.body_ratio * 100
        limb_h = proportions.limb_ratio * 100
        total = head_h + body_h + limb_h
        scale = proportions.scale

        # Draw simple stick figure
        cx = 50
        head_y = total
        body_top = total - head_h
        body_bot = body_top - body_h

        circle = plt.Circle((cx, head_y - head_h / 2), head_h / 2 * scale,
                             fill=False, linewidth=2, color="darkblue")
        ax2.add_patch(circle)
        ax2.plot([cx, cx], [body_top, body_bot], "b-", linewidth=3)
        # Arms
        sq = proportions.squash_stretch
        ax2.plot([cx - 15 * sq, cx, cx + 15 * sq], [body_top - 5, body_top, body_top - 5],
                 "b-", linewidth=2)
        # Legs
        ax2.plot([cx - 10, cx, cx + 10], [body_bot - limb_h, body_bot, body_bot - limb_h],
                 "b-", linewidth=2)
        ax2.set_xlim(0, 100)
        ax2.set_ylim(-20, total + 20)
        ax2.set_title(f"Proportions\nscale={scale:.2f} squash={sq:.2f}")
        ax2.set_aspect("equal")
        ax2.grid(True, alpha=0.3)

        # Panel 3: Animation timing curve
        ax3 = axes[2]
        t_norm = np.linspace(0, 1.0, 200)
        # Bezier-ish ease curve
        ease_in = anim.ease_in
        ease_out = anim.ease_out
        curve = t_norm ** (1.0 + ease_in * 3) * (1 - (1 - t_norm) ** (1.0 + ease_out * 3))
        curve = curve / max(curve.max(), 1e-6)
        # Apply exaggeration
        curve = curve * anim.exaggeration
        ax3.plot(t_norm, curve, "r-", linewidth=2)
        ax3.set_title(f"Animation Curve\nexagg={anim.exaggeration:.2f} fps={anim.frame_rate}")
        ax3.set_xlabel("Normalized Time")
        ax3.set_ylabel("Value")
        ax3.grid(True, alpha=0.3)

        plt.tight_layout()

        if output_path is None:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix="proxy_")
            output_path = Path(tmp.name)
            tmp.close()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(output_path), dpi=96, bbox_inches="tight")
        plt.close(fig)

        logger.info("Proxy rendered → %s", output_path)
        return output_path


# ---------------------------------------------------------------------------
# Parameter adjustment helpers
# ---------------------------------------------------------------------------

_AMPLIFY_FACTOR = 0.20   # +20% per "[+]" round
_DAMPEN_FACTOR = 0.15    # -15% per "[-]" round


def amplify_genotype(genotype: "Genotype") -> "Genotype":
    """Return a new genotype with exaggerated parameters (+20%)."""
    from ..workspace.director_intent import Genotype
    g = copy.deepcopy(genotype)
    flat = g.flat_params()
    for key in flat:
        if "exaggeration" in key or "bounce" in key or "squash_stretch" in key:
            flat[key] *= (1.0 + _AMPLIFY_FACTOR * 1.5)
        elif "stiffness" in key:
            flat[key] *= (1.0 + _AMPLIFY_FACTOR)
        elif "anticipation" in key or "follow_through" in key:
            flat[key] *= (1.0 + _AMPLIFY_FACTOR * 0.8)
        else:
            flat[key] *= (1.0 + _AMPLIFY_FACTOR * 0.5)
    g.apply_flat_params(flat)
    return g


def dampen_genotype(genotype: "Genotype") -> "Genotype":
    """Return a new genotype with dampened parameters (-15%)."""
    from ..workspace.director_intent import Genotype
    g = copy.deepcopy(genotype)
    flat = g.flat_params()
    for key in flat:
        if "exaggeration" in key or "bounce" in key or "squash_stretch" in key:
            flat[key] *= (1.0 - _DAMPEN_FACTOR * 1.5)
        elif "stiffness" in key:
            flat[key] *= (1.0 - _DAMPEN_FACTOR)
        elif "anticipation" in key or "follow_through" in key:
            flat[key] *= (1.0 - _DAMPEN_FACTOR * 0.8)
        else:
            flat[key] *= (1.0 - _DAMPEN_FACTOR * 0.5)
    g.apply_flat_params(flat)
    return g


# ---------------------------------------------------------------------------
# Feedback record
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRound:
    """Record of a single REPL feedback round."""
    round_number: int
    action: str  # "amplify", "dampen", "approve", "abort"
    genotype_snapshot: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interactive Gate Result
# ---------------------------------------------------------------------------

@dataclass
class InteractiveGateResult:
    """Complete result of the interactive preview gate session."""
    decision: GateDecision
    final_genotype: Optional["Genotype"] = None
    feedback_history: List[FeedbackRound] = field(default_factory=list)
    proxy_path: Optional[str] = None
    blueprint_path: Optional[str] = None
    total_rounds: int = 0


# ---------------------------------------------------------------------------
# Interactive Gate (the REPL)
# ---------------------------------------------------------------------------

class InteractivePreviewGate:
    """Multi-round REPL preview gate with Blueprint sedimentation.

    This gate is the **mandatory checkpoint** between intent parsing and
    heavy rendering.  It generates sub-second proxy previews and collects
    iterative human feedback before authorizing GPU work.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        renderer: Optional[ProxyRenderer] = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.renderer = renderer or ProxyRenderer()
        self.input_fn = input_fn
        self.output_fn = output_fn

    def run(self, spec: "CreatorIntentSpec") -> InteractiveGateResult:
        """Execute the interactive preview REPL loop.

        Parameters
        ----------
        spec : CreatorIntentSpec
            The strongly-typed intent specification from the director parser.

        Returns
        -------
        InteractiveGateResult
            Contains the gate decision, final genotype, feedback history,
            and optional blueprint path.
        """
        from ..workspace.director_intent import CreatorIntentSpec, Blueprint, BlueprintMeta, Genotype

        current_genotype = copy.deepcopy(spec.genotype)
        history: List[FeedbackRound] = []
        round_num = 0
        proxy_path: Optional[Path] = None

        while True:
            round_num += 1

            # Generate proxy preview
            try:
                proxy_path = self.renderer.render_proxy(
                    current_genotype,
                    output_path=self.workspace_root / "workspace" / "proxy" / f"proxy_round_{round_num}.png",
                )
                self.output_fn(f"\n🎬【白模已生成】→ {proxy_path}")
            except Exception as e:
                self.output_fn(f"\n⚠️ 白模生成失败: {e}")
                logger.warning("Proxy render failed: %s", e)

            # Show parameter summary
            flat = current_genotype.flat_params()
            self.output_fn("当前核心参数:")
            for key in sorted(flat.keys()):
                self.output_fn(f"  {key}: {flat[key]:.4f}")

            # REPL prompt
            self.output_fn("\n请选择：")
            self.output_fn("  [1] ✅ 完美出图")
            self.output_fn("  [2] [+] 再夸张点")
            self.output_fn("  [3] [-] 收敛点")
            self.output_fn("  [4] ❌ 退出")

            choice = self.input_fn("输入选项编号: ").strip()

            if choice == "1":
                # APPROVED
                history.append(FeedbackRound(
                    round_number=round_num,
                    action="approve",
                    genotype_snapshot=current_genotype.to_dict(),
                ))

                # Ask about Blueprint save
                bp_path = self._offer_blueprint_save(current_genotype)
                decision = GateDecision.BLUEPRINT_SAVED if bp_path else GateDecision.APPROVED

                return InteractiveGateResult(
                    decision=decision,
                    final_genotype=current_genotype,
                    feedback_history=history,
                    proxy_path=str(proxy_path) if proxy_path else None,
                    blueprint_path=str(bp_path) if bp_path else None,
                    total_rounds=round_num,
                )

            elif choice == "2":
                # AMPLIFY
                current_genotype = amplify_genotype(current_genotype)
                history.append(FeedbackRound(
                    round_number=round_num,
                    action="amplify",
                    genotype_snapshot=current_genotype.to_dict(),
                ))
                self.output_fn("🔥 参数已放大，重新生成白模...")

            elif choice == "3":
                # DAMPEN
                current_genotype = dampen_genotype(current_genotype)
                history.append(FeedbackRound(
                    round_number=round_num,
                    action="dampen",
                    genotype_snapshot=current_genotype.to_dict(),
                ))
                self.output_fn("🧊 参数已收敛，重新生成白模...")

            elif choice == "4":
                # ABORT
                history.append(FeedbackRound(
                    round_number=round_num,
                    action="abort",
                    genotype_snapshot=current_genotype.to_dict(),
                ))
                self.output_fn("❌ 已退出预演。")
                return InteractiveGateResult(
                    decision=GateDecision.ABORTED,
                    final_genotype=current_genotype,
                    feedback_history=history,
                    proxy_path=str(proxy_path) if proxy_path else None,
                    total_rounds=round_num,
                )
            else:
                self.output_fn("⚠️ 输入无效，请输入 1-4 的编号。")
                round_num -= 1  # Don't count invalid input as a round

    def _offer_blueprint_save(self, genotype: "Genotype") -> Optional[Path]:
        """After approval, offer to save the converged genotype as a Blueprint.

        Returns the path to the saved Blueprint YAML, or None if declined.
        """
        from ..workspace.director_intent import Blueprint, BlueprintMeta

        self.output_fn("\n💾 状态极佳！是否将当前绝佳参数保存为可复用的【蓝图模板 Blueprint】？")
        self.output_fn("  [Y] 保存蓝图")
        self.output_fn("  [N] 跳过")

        save_choice = self.input_fn("请选择 [Y/N]: ").strip().upper()

        if save_choice not in ("Y", "YES"):
            return None

        name = self.input_fn("请为蓝图命名 (英文, 如 hero_v1): ").strip()
        if not name:
            name = "unnamed_blueprint"

        # Sanitize name
        name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in name)

        blueprint = Blueprint(
            meta=BlueprintMeta(
                name=name,
                description=f"Auto-saved from Director Studio interactive session",
            ),
            genotype=copy.deepcopy(genotype),
        )

        bp_dir = self.workspace_root / "workspace" / "blueprints"
        bp_path = bp_dir / f"{name}.yaml"
        blueprint.save_yaml(bp_path)

        self.output_fn(f"✅ 蓝图已保存 → {bp_path}")
        return bp_path


# ---------------------------------------------------------------------------
# Non-interactive gate (for programmatic / test usage)
# ---------------------------------------------------------------------------

class ProgrammaticPreviewGate:
    """Non-interactive version of the preview gate for testing and automation.

    Accepts a sequence of pre-programmed choices instead of reading from stdin.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        choices: Optional[List[str]] = None,
        blueprint_name: str = "test_blueprint",
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.choices = list(choices or ["1", "Y", "test_blueprint"])
        self._choice_index = 0
        self.output_log: List[str] = []

    def _input_fn(self, prompt: str) -> str:
        if self._choice_index < len(self.choices):
            val = self.choices[self._choice_index]
            self._choice_index += 1
            return val
        return "4"  # Default to abort if choices exhausted

    def _output_fn(self, msg: str) -> None:
        self.output_log.append(msg)

    def run(self, spec: "CreatorIntentSpec") -> InteractiveGateResult:
        gate = InteractivePreviewGate(
            workspace_root=self.workspace_root,
            input_fn=self._input_fn,
            output_fn=self._output_fn,
        )
        return gate.run(spec)


__all__ = [
    "GateDecision",
    "InteractiveGateResult",
    "InteractivePreviewGate",
    "FeedbackRound",
    "ProgrammaticPreviewGate",
    "ProxyRenderer",
    "amplify_genotype",
    "dampen_genotype",
]

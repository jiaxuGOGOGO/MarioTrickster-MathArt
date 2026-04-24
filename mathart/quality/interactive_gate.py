"""Interactive Preview Gate — Knowledge-Aware Multi-Round REPL & Blueprint Sedimentation.

SESSION-139: P0-SESSION-136-DIRECTOR-STUDIO-V2
SESSION-140: P0-SESSION-137-KNOWLEDGE-SYNERGY-BRIDGE

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

SESSION-140 upgrade — Conflict Arbitration (Truth Gateway):
- After each amplify/dampen operation, the gate checks the resulting
  parameters against the RuntimeDistillationBus constraints.
- If violations are detected, the gate suspends and displays a
  **Truth Gateway Warning** with three-tier severity:
  - FATAL: mathematical impossibility → hard block (no override)
  - PHYSICAL: knowledge boundary violation → warn + offer override
  - INFO: style suggestion → log only
- The user can choose: [1] Comply (auto-clamp) or [2] Override (force)
- This preserves artistic freedom while exposing scientific conflicts.

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
- **防知识过拟合死锁红线**: Knowledge constraints never hard-block artistic
  override for PHYSICAL violations. Only FATAL (math errors) are hard-blocked.

External research anchors:
- SESSION-139: DreamCrafter, Pixar animatic pipeline, Interactive GA
- SESSION-140: Constraint Reconciliation (arXiv 2511.10952), Human-AI
  Interface Layers (ProQuest 2025), Boundary-Conditioned Inpainting (SIGGRAPH 2025)
"""
from __future__ import annotations

import copy
import io
import logging
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from ..distill.runtime_bus import RuntimeDistillationBus, CompiledParameterSpace

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
# Knowledge Conflict Arbitration Result
# ---------------------------------------------------------------------------

@dataclass
class ConflictArbitrationResult:
    """Result of knowledge conflict arbitration during interactive preview.

    SESSION-140: Tracks whether the user chose to comply with knowledge
    constraints or override them, and which parameters were affected.
    """
    had_conflicts: bool = False
    user_chose_comply: bool = True
    violations: List[Dict[str, Any]] = field(default_factory=list)
    overridden_params: List[str] = field(default_factory=list)
    clamped_params: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "had_conflicts": self.had_conflicts,
            "user_chose_comply": self.user_chose_comply,
            "violations": self.violations,
            "overridden_params": self.overridden_params,
            "clamped_params": self.clamped_params,
        }


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

        cx = 50
        head_y = total
        body_top = total - head_h
        body_bot = body_top - body_h

        circle = plt.Circle((cx, head_y - head_h / 2), head_h / 2 * scale,
                             fill=False, linewidth=2, color="darkblue")
        ax2.add_patch(circle)
        ax2.plot([cx, cx], [body_top, body_bot], "b-", linewidth=3)
        sq = proportions.squash_stretch
        ax2.plot([cx - 15 * sq, cx, cx + 15 * sq], [body_top - 5, body_top, body_top - 5],
                 "b-", linewidth=2)
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
        ease_in = anim.ease_in
        ease_out = anim.ease_out
        curve = t_norm ** (1.0 + ease_in * 3) * (1 - (1 - t_norm) ** (1.0 + ease_out * 3))
        curve = curve / max(curve.max(), 1e-6)
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
# Knowledge Conflict Checker
# ---------------------------------------------------------------------------

def check_knowledge_conflicts(
    genotype: "Genotype",
    knowledge_bus: Optional["RuntimeDistillationBus"],
) -> List[Dict[str, Any]]:
    """Check a genotype's parameters against knowledge constraints.

    SESSION-140: Returns a list of violation dicts, each containing:
    - param_key: the violated parameter
    - user_value: the current value
    - knowledge_min / knowledge_max: the constraint boundaries
    - clamped_value: what the value would be if clamped
    - severity: "fatal" (hard constraint) or "physical" (soft)
    - rule_description: human-readable description

    Returns an empty list if no violations or no knowledge bus.
    """
    if knowledge_bus is None:
        return []

    flat = genotype.flat_params()
    violations: List[Dict[str, Any]] = []

    for module_name, compiled in knowledge_bus.compiled_spaces.items():
        for idx, param_name in enumerate(compiled.param_names):
            # Try to match against flat params
            matched_key = _resolve_param_key(param_name, flat)
            if matched_key is None:
                continue

            value = flat[matched_key]
            has_min = bool(compiled.has_min[idx])
            has_max = bool(compiled.has_max[idx])
            min_val = float(compiled.min_values[idx]) if has_min else None
            max_val = float(compiled.max_values[idx]) if has_max else None
            is_hard = bool(compiled.hard_mask[idx])

            violated = False
            clamped_value = value
            if has_min and value < min_val:
                clamped_value = min_val
                violated = True
            if has_max and value > max_val:
                clamped_value = max_val
                violated = True

            if violated:
                violations.append({
                    "param_key": matched_key,
                    "user_value": value,
                    "knowledge_min": min_val,
                    "knowledge_max": max_val,
                    "clamped_value": clamped_value,
                    "severity": "fatal" if is_hard else "physical",
                    "is_hard": is_hard,
                    "rule_description": f"Distilled from {module_name}: {param_name}",
                    "rule_id": f"{module_name}.{param_name}",
                })

    return violations


def apply_knowledge_clamp_to_genotype(
    genotype: "Genotype",
    violations: List[Dict[str, Any]],
) -> "Genotype":
    """Apply knowledge clamping to a genotype based on detected violations.

    Returns a new genotype with clamped values.
    """
    g = copy.deepcopy(genotype)
    flat = g.flat_params()
    for v in violations:
        key = v["param_key"]
        if key in flat:
            flat[key] = v["clamped_value"]
    g.apply_flat_params(flat)
    return g


def _resolve_param_key(
    knowledge_param: str, flat: Dict[str, float]
) -> Optional[str]:
    """Resolve a knowledge parameter name to a flat genotype key."""
    if knowledge_param in flat:
        return knowledge_param
    leaf = knowledge_param.split(".")[-1]
    candidates = [k for k in flat if k.endswith(f".{leaf}")]
    if len(candidates) == 1:
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Feedback record
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRound:
    """Record of a single REPL feedback round."""
    round_number: int
    action: str  # "amplify", "dampen", "approve", "abort", "knowledge_comply", "knowledge_override"
    genotype_snapshot: Dict[str, Any] = field(default_factory=dict)
    conflict_arbitration: Optional[ConflictArbitrationResult] = None


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
    # SESSION-140: aggregate conflict arbitration data
    conflict_arbitrations: List[ConflictArbitrationResult] = field(default_factory=list)
    knowledge_overrides_count: int = 0
    knowledge_compliances_count: int = 0


# ---------------------------------------------------------------------------
# Interactive Gate (the REPL) — Knowledge-Aware
# ---------------------------------------------------------------------------

class InteractivePreviewGate:
    """Multi-round REPL preview gate with Blueprint sedimentation and
    Knowledge Conflict Arbitration.

    SESSION-140: This gate now accepts an optional ``RuntimeDistillationBus``.
    After each amplify/dampen operation, it checks the resulting parameters
    against knowledge constraints and displays Truth Gateway Warnings when
    violations are detected.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        renderer: Optional[ProxyRenderer] = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.renderer = renderer or ProxyRenderer()
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.knowledge_bus = knowledge_bus

    def _arbitrate_conflicts(
        self, genotype: "Genotype"
    ) -> Tuple["Genotype", ConflictArbitrationResult]:
        """Check genotype against knowledge constraints and arbitrate.

        SESSION-140: Truth Gateway Warning system.

        Returns
        -------
        tuple of (possibly_clamped_genotype, arbitration_result)
        """
        violations = check_knowledge_conflicts(genotype, self.knowledge_bus)
        result = ConflictArbitrationResult()

        if not violations:
            return genotype, result

        result.had_conflicts = True
        result.violations = violations

        # Display Truth Gateway Warning
        self.output_fn("\n" + "=" * 60)
        self.output_fn("  ⚠️ 【真理网关警告 — Truth Gateway Warning】")
        self.output_fn("=" * 60)

        fatal_violations = [v for v in violations if v["severity"] == "fatal"]
        physical_violations = [v for v in violations if v["severity"] == "physical"]

        for v in violations:
            severity_icon = "🚫" if v["severity"] == "fatal" else "⚠️"
            self.output_fn(
                f"  {severity_icon} {v['param_key']}: 当前值 {v['user_value']:.4f} "
                f"违反了蒸馏知识边界 [{v['knowledge_min']}, {v['knowledge_max']}]"
            )
            self.output_fn(f"     来源: {v['rule_description']}")
            self.output_fn(f"     安全裁剪值: {v['clamped_value']:.4f}")

        if fatal_violations:
            # FATAL: hard block — no override allowed
            self.output_fn(
                "\n🚫 检测到致命约束违规（数学/物理不可能），系统将自动安全裁剪。"
            )
            # SESSION-146: Log fatal knowledge violations into blackbox.
            logger.warning(
                "Truth Gateway FATAL block — %d violations auto-clamped: %s",
                len(fatal_violations),
                [v["param_key"] for v in fatal_violations],
            )
            genotype = apply_knowledge_clamp_to_genotype(genotype, violations)
            result.user_chose_comply = True
            result.clamped_params = [v["param_key"] for v in violations]
            self.output_fn("✅ 已自动安全裁剪至知识边界内。")
        elif physical_violations:
            # PHYSICAL: warn + offer override
            self.output_fn("\n您的设定违反了蒸馏出的安全极限，强行渲染可能导致穿模或光流崩坏。")
            self.output_fn("请选择：")
            self.output_fn("  [1] 遵从科学 — 由系统自动安全裁剪")
            self.output_fn("  [2] 人类意图覆盖 — 无视知识强行生成")

            choice = self.input_fn("请选择 [1/2]: ").strip()

            if choice == "2":
                # User overrides knowledge
                result.user_chose_comply = False
                result.overridden_params = [v["param_key"] for v in physical_violations]
                self.output_fn("🎨 艺术自由优先 — 已保留您的参数设定。")
                logger.warning(
                    "Truth Gateway: user OVERRODE knowledge constraints for: %s",
                    result.overridden_params,
                )
            else:
                # User complies
                genotype = apply_knowledge_clamp_to_genotype(genotype, violations)
                result.user_chose_comply = True
                result.clamped_params = [v["param_key"] for v in violations]
                self.output_fn("✅ 已安全裁剪至知识边界内。")
                logger.info(
                    "Truth Gateway: user COMPLIED with knowledge clamp for: %s",
                    result.clamped_params,
                )

        self.output_fn("=" * 60 + "\n")
        return genotype, result

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
        conflict_arbitrations: List[ConflictArbitrationResult] = []
        knowledge_overrides = 0
        knowledge_compliances = 0
        round_num = 0
        proxy_path: Optional[Path] = None

        # Initial knowledge conflict check (from intent parsing)
        if self.knowledge_bus is not None:
            current_genotype, initial_arb = self._arbitrate_conflicts(current_genotype)
            if initial_arb.had_conflicts:
                conflict_arbitrations.append(initial_arb)
                if initial_arb.user_chose_comply:
                    knowledge_compliances += 1
                else:
                    knowledge_overrides += 1

        while True:
            round_num += 1

            # Generate proxy preview
            try:
                proxy_path = self.renderer.render_proxy(
                    current_genotype,
                    output_path=self.workspace_root / "workspace" / "proxy" / f"proxy_round_{round_num}.png",
                )
                self.output_fn(f"\n🎬【白模已生成】→ {proxy_path}")
                logger.info("Proxy rendered successfully: round=%d, path=%s", round_num, proxy_path)
            except Exception as e:
                self.output_fn(f"\n⚠️ 白模生成失败: {e}")
                # SESSION-146: Capture the full traceback in the blackbox
                # so that proxy render failures are never lost.
                logger.warning(
                    "Proxy render FAILED at round %d — degraded to parameter view",
                    round_num,
                    exc_info=True,
                )

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
                    conflict_arbitrations=conflict_arbitrations,
                    knowledge_overrides_count=knowledge_overrides,
                    knowledge_compliances_count=knowledge_compliances,
                )

            elif choice == "2":
                # AMPLIFY
                current_genotype = amplify_genotype(current_genotype)

                # SESSION-140: Check knowledge conflicts after amplification
                arb_result = None
                if self.knowledge_bus is not None:
                    current_genotype, arb_result = self._arbitrate_conflicts(
                        current_genotype
                    )
                    if arb_result.had_conflicts:
                        conflict_arbitrations.append(arb_result)
                        if arb_result.user_chose_comply:
                            knowledge_compliances += 1
                        else:
                            knowledge_overrides += 1

                history.append(FeedbackRound(
                    round_number=round_num,
                    action="amplify",
                    genotype_snapshot=current_genotype.to_dict(),
                    conflict_arbitration=arb_result,
                ))
                self.output_fn("🔥 参数已放大，重新生成白模...")

            elif choice == "3":
                # DAMPEN
                current_genotype = dampen_genotype(current_genotype)

                # SESSION-140: Check knowledge conflicts after dampening
                arb_result = None
                if self.knowledge_bus is not None:
                    current_genotype, arb_result = self._arbitrate_conflicts(
                        current_genotype
                    )
                    if arb_result.had_conflicts:
                        conflict_arbitrations.append(arb_result)
                        if arb_result.user_chose_comply:
                            knowledge_compliances += 1
                        else:
                            knowledge_overrides += 1

                history.append(FeedbackRound(
                    round_number=round_num,
                    action="dampen",
                    genotype_snapshot=current_genotype.to_dict(),
                    conflict_arbitration=arb_result,
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
                    conflict_arbitrations=conflict_arbitrations,
                    knowledge_overrides_count=knowledge_overrides,
                    knowledge_compliances_count=knowledge_compliances,
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

        # SESSION-179/180: Blueprint Vault — Custom Naming with Timestamp Fallback
        name = self.input_fn(
            "[💾] 请为这个动作命名: "
        ).strip()
        if not name:
            import datetime
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"blueprint_{ts}"
            self.output_fn(f"\033[90m    ↳ 自动生成蓝图名: {name}\033[0m")

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

    SESSION-140: Now supports knowledge bus injection for conflict arbitration
    testing.
    """

    def __init__(
        self,
        workspace_root: Path | str | None = None,
        choices: Optional[List[str]] = None,
        blueprint_name: str = "test_blueprint",
        knowledge_bus: Optional["RuntimeDistillationBus"] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.choices = list(choices or ["1", "Y", "test_blueprint"])
        self._choice_index = 0
        self.output_log: List[str] = []
        self.knowledge_bus = knowledge_bus

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
            knowledge_bus=self.knowledge_bus,
        )
        return gate.run(spec)


__all__ = [
    "ConflictArbitrationResult",
    "GateDecision",
    "InteractiveGateResult",
    "InteractivePreviewGate",
    "FeedbackRound",
    "ProgrammaticPreviewGate",
    "ProxyRenderer",
    "amplify_genotype",
    "apply_knowledge_clamp_to_genotype",
    "check_knowledge_conflicts",
    "dampen_genotype",
]

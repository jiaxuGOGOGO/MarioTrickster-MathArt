"""SESSION-156: Knowledge Triage & Native Deduplication Funnel.

This module implements the **Knowledge Triage** system — a pre-compilation
funnel that classifies incoming knowledge into two strictly separated tiers
before it can enter the Auto-Compiler pipeline:

  **[Actionable-Rule] (Micro/Tier 1&2)**
    Quantifiable, code-compilable constraints such as physics thresholds,
    color numeric limits, pixel art forbidden patterns, timing values.
    ONLY these rules are allowed to pass through the funnel into the
    Auto-Compiler for Python code synthesis.

  **[Macro-Guidance] (Macro/Tier 3)**
    Abstract design philosophy, worldview, game feel theory, perspective
    theory, narrative principles.  These are archived into the knowledge
    base with a ``[Macro-Guidance]`` tag but are **physically blocked**
    from entering the Auto-Compiler.  They serve as high-dimensional
    context for future LLM reasoning, never as executable code.

The module also integrates the project's **native DeduplicationEngine**
(``mathart.distill.deduplication``) as a mandatory pre-filter.  No rule
reaches the knowledge base or the compiler without first passing through
the three-tier dedup check (exact hash → semantic cosine → parameter merge).

Pipeline position::

    Raw Text → LLM/Heuristic Extraction → **Dedup Funnel** → **Triage Gate**
    → [Actionable] → Knowledge Files + Auto-Compiler
    → [Macro]      → Knowledge Files (tagged) + BLOCKED from compiler

Architecture red lines:
  - ✅ Zero new dedup code: imports and calls ``DeduplicationEngine``
  - ✅ Physical pipeline isolation: ``if tier == 'Macro': continue``
  - ✅ No destruction of existing Auto-Compiler or enforcer registry
  - ✅ Full UX transparency: every decision is logged to terminal
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from mathart.distill.deduplication import DeduplicationEngine, DedupResult


# ─────────────────────────────────────────────────────────────────────────────
# Triage Classification
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeTier(str, Enum):
    """Classification tier for distilled knowledge."""

    ACTIONABLE = "Actionable-Rule"   # Micro/Tier 1&2 — compilable to code
    MACRO      = "Macro-Guidance"    # Macro/Tier 3   — archive only


@dataclass
class TriageDecision:
    """Record of a single triage classification decision."""
    rule_text: str
    tier: KnowledgeTier
    confidence: float
    reason: str
    signals: list[str] = field(default_factory=list)

    def ux_line(self) -> str:
        """Format a user-facing terminal line."""
        if self.tier == KnowledgeTier.ACTIONABLE:
            return (
                f"\033[32m  [⚖️ 知识分诊] 判定为【微观约束 Actionable-Rule】，"
                f"送入 Python 编译引擎...\033[0m"
                f"\n         规则: {self.rule_text[:80]}"
                f"\n         信号: {', '.join(self.signals[:3])}"
            )
        else:
            return (
                f"\033[33m  [⚖️ 知识分诊] 判定为【宏观哲学 Macro-Guidance】，"
                f"安全归档，跳过代码生成...\033[0m"
                f"\n         规则: {self.rule_text[:80]}"
                f"\n         原因: {self.reason}"
            )


@dataclass
class TriageResult:
    """Aggregated result of a triage session."""
    total_input: int
    actionable_count: int
    macro_count: int
    decisions: list[TriageDecision] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"TriageResult: {self.total_input} rules → "
            f"{self.actionable_count} actionable (→ compiler), "
            f"{self.macro_count} macro (→ archive only)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Triage Engine
# ─────────────────────────────────────────────────────────────────────────────

# Signals that indicate ACTIONABLE (Micro) knowledge — quantifiable constraints
_ACTIONABLE_SIGNALS: list[re.Pattern] = [
    re.compile(r"\b\d+\.?\d*\s*(?:°|deg|px|ms|fps|frames?|%)\b", re.I),
    re.compile(r"\b(?:min|max|range|threshold|limit|clamp|cap)\b", re.I),
    re.compile(r"\b(?:gravity|spring_k|damping|friction|mass|velocity)\b", re.I),
    re.compile(r"\b(?:canvas_size|palette_size|dither|interpolation)\b", re.I),
    re.compile(r"\b(?:lightness|chroma|hue|saturation|contrast)\b", re.I),
    re.compile(r"\b(?:frame_rate|cycle_frames|ease_in|ease_out)\b", re.I),
    re.compile(r"[a-zA-Z_]\w*\s*[=:]\s*[\d.]+", re.I),  # param = value patterns
    re.compile(r"\b(?:must not exceed|must be between|should be at least)\b", re.I),
    re.compile(r"\b(?:禁止|必须|不得超过|不得低于|阈值|上限|下限|范围)\b"),
    re.compile(r"\b(?:constraint|enforce|validate|assert|require)\b", re.I),
    re.compile(r"\b(?:pixel|sprite|tile|resolution|width|height)\b", re.I),
    re.compile(r"\b(?:angle|radius|distance|offset|margin|padding)\b", re.I),
]

# Signals that indicate MACRO (philosophical/abstract) knowledge
_MACRO_SIGNALS: list[re.Pattern] = [
    re.compile(r"\b(?:philosophy|philosophical|theory|theoretical)\b", re.I),
    re.compile(r"\b(?:worldview|world.?building|narrative|storytelling)\b", re.I),
    re.compile(r"\b(?:game design principle|design philosophy|core pillar)\b", re.I),
    re.compile(r"\b(?:player experience|emotional|immersion|engagement)\b", re.I),
    re.compile(r"\b(?:aesthetic|beauty|elegance|harmony|balance)\b", re.I),
    re.compile(r"\b(?:fun|enjoyment|satisfaction|flow state|心流)\b", re.I),
    re.compile(r"\b(?:perspective theory|vanishing point theory|composition rule)\b", re.I),
    re.compile(r"\b(?:哲学|世界观|设计理念|核心体验|叙事|美学|乐趣)\b"),
    re.compile(r"\b(?:should feel|feels like|sense of|impression of)\b", re.I),
    re.compile(r"\b(?:general guideline|best practice|rule of thumb)\b", re.I),
    re.compile(r"\b(?:abstract|conceptual|high.?level|overarching|holistic)\b", re.I),
    re.compile(r"\b(?:游戏必须|游戏应该|好的游戏|优秀的|体验感)\b"),
]


class KnowledgeTriageEngine:
    """Classifies distilled knowledge rules into Actionable vs Macro tiers.

    The engine uses a signal-counting heuristic:
      1. Count matches against ``_ACTIONABLE_SIGNALS`` and ``_MACRO_SIGNALS``.
      2. Check for the presence of numeric parameters (strong actionable signal).
      3. If actionable signals dominate → ``KnowledgeTier.ACTIONABLE``.
      4. If macro signals dominate or no quantifiable content → ``KnowledgeTier.MACRO``.

    The classification is deliberately conservative: when in doubt, rules are
    classified as MACRO (safe archive) rather than ACTIONABLE (risky compilation).
    This prevents abstract philosophy from being force-compiled into broken code.

    Parameters
    ----------
    actionable_threshold : float
        Minimum ratio of actionable-to-total signals to classify as ACTIONABLE.
        Default 0.4 — a rule needs at least 40% actionable signals.
    verbose : bool
        Print triage decisions to stdout.
    """

    def __init__(
        self,
        actionable_threshold: float = 0.4,
        verbose: bool = True,
    ) -> None:
        self.actionable_threshold = actionable_threshold
        self.verbose = verbose

    def classify_rule(
        self,
        rule_text: str,
        params: dict,
        rule_type: str = "",
    ) -> TriageDecision:
        """Classify a single rule into Actionable or Macro tier.

        Parameters
        ----------
        rule_text : str
            The human-readable rule description.
        params : dict
            Extracted parameter key-value pairs (strong actionable signal).
        rule_type : str
            Original rule_type from distillation ('hard_constraint', etc.).

        Returns
        -------
        TriageDecision
        """
        actionable_hits: list[str] = []
        macro_hits: list[str] = []

        # Check actionable signals
        for pattern in _ACTIONABLE_SIGNALS:
            matches = pattern.findall(rule_text)
            if matches:
                actionable_hits.append(pattern.pattern[:40])

        # Check macro signals
        for pattern in _MACRO_SIGNALS:
            matches = pattern.findall(rule_text)
            if matches:
                macro_hits.append(pattern.pattern[:40])

        # Strong actionable signal: has numeric parameters
        has_params = bool(params) and any(
            self._is_numeric(v) for v in params.values()
        )
        if has_params:
            actionable_hits.append("numeric_params_present")

        # Strong actionable signal: rule_type is hard_constraint
        if rule_type == "hard_constraint":
            actionable_hits.append("hard_constraint_type")

        # Decision logic
        total_signals = len(actionable_hits) + len(macro_hits)
        if total_signals == 0:
            # No signals at all — if it has params, it's actionable; else macro
            if has_params:
                tier = KnowledgeTier.ACTIONABLE
                confidence = 0.6
                reason = "Has numeric parameters but no keyword signals."
            else:
                tier = KnowledgeTier.MACRO
                confidence = 0.5
                reason = "No quantifiable signals detected — defaulting to safe archive."
        else:
            actionable_ratio = len(actionable_hits) / total_signals
            if actionable_ratio >= self.actionable_threshold:
                tier = KnowledgeTier.ACTIONABLE
                confidence = min(0.95, 0.5 + actionable_ratio * 0.5)
                reason = (
                    f"Actionable signals ({len(actionable_hits)}) dominate "
                    f"over macro signals ({len(macro_hits)})."
                )
            else:
                tier = KnowledgeTier.MACRO
                confidence = min(0.95, 0.5 + (1 - actionable_ratio) * 0.5)
                reason = (
                    f"Macro signals ({len(macro_hits)}) dominate "
                    f"over actionable signals ({len(actionable_hits)}). "
                    "Blocking from Auto-Compiler."
                )

        return TriageDecision(
            rule_text=rule_text,
            tier=tier,
            confidence=confidence,
            reason=reason,
            signals=actionable_hits + [f"[macro]{s}" for s in macro_hits],
        )

    def triage_batch(
        self,
        rules: list[tuple[str, dict, str]],  # (rule_text, params, rule_type)
    ) -> tuple[list[int], list[int], TriageResult]:
        """Classify a batch of rules and return indices for each tier.

        Parameters
        ----------
        rules : list of (rule_text, params, rule_type)

        Returns
        -------
        actionable_indices : list[int]
            Indices of rules classified as ACTIONABLE.
        macro_indices : list[int]
            Indices of rules classified as MACRO.
        result : TriageResult
            Aggregated triage statistics.
        """
        actionable_indices: list[int] = []
        macro_indices: list[int] = []
        decisions: list[TriageDecision] = []

        for i, (rule_text, params, rule_type) in enumerate(rules):
            decision = self.classify_rule(rule_text, params, rule_type)
            decisions.append(decision)

            # ── PHYSICAL PIPELINE ISOLATION ──────────────────────────────
            # This is the hard gate: Macro knowledge NEVER passes through.
            if decision.tier == KnowledgeTier.MACRO:
                macro_indices.append(i)
                if self.verbose:
                    print(decision.ux_line())
                continue  # ← BLOCKED from compiler pipeline

            # Only ACTIONABLE rules reach here
            actionable_indices.append(i)
            if self.verbose:
                print(decision.ux_line())

        result = TriageResult(
            total_input=len(rules),
            actionable_count=len(actionable_indices),
            macro_count=len(macro_indices),
            decisions=decisions,
        )

        if self.verbose:
            print(f"\033[36m  [⚖️ 分诊汇总] {result.summary()}\033[0m")

        return actionable_indices, macro_indices, result

    @staticmethod
    def _is_numeric(value) -> bool:
        """Check if a value is numeric or looks numeric."""
        if isinstance(value, (int, float)):
            return True
        try:
            float(str(value).split("-")[0].split("~")[0].strip())
            return True
        except (ValueError, TypeError, IndexError):
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Integrated Funnel: Dedup + Triage
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeFunnel:
    """Orchestrates the complete pre-compilation funnel:
    Deduplication → Triage → Route.

    This is the single entry point that the ``OuterLoopDistiller`` calls
    before any knowledge reaches the knowledge base or the Auto-Compiler.

    The funnel enforces two invariants:
      1. **Zero redundancy**: All rules pass through ``DeduplicationEngine``
         before any further processing.
      2. **Physical isolation**: Only ``[Actionable-Rule]`` tagged rules
         are allowed to proceed to the Auto-Compiler.  ``[Macro-Guidance]``
         rules are archived but physically blocked from code generation.

    Parameters
    ----------
    project_root : Path-like
        Project root directory.
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        project_root=None,
        verbose: bool = True,
    ) -> None:
        from pathlib import Path
        self.project_root = Path(project_root) if project_root else Path(".")
        self.verbose = verbose

        # ── Reuse native DeduplicationEngine — ZERO new dedup code ──
        self.dedup_engine = DeduplicationEngine(
            project_root=self.project_root,
            verbose=verbose,
        )
        self.dedup_engine.load_existing()

        # ── Triage engine ──
        self.triage_engine = KnowledgeTriageEngine(verbose=verbose)

    def process(
        self,
        rules: list[tuple[str, str, dict, str]],
        # Each tuple: (domain, rule_text, params, rule_type)
        source_name: str = "",
    ) -> "FunnelResult":
        """Run the complete funnel: Dedup → Triage → Route.

        Parameters
        ----------
        rules : list of (domain, rule_text, params, rule_type)
        source_name : str
            Source document name for logging.

        Returns
        -------
        FunnelResult
            Contains separated actionable/macro rules and all statistics.
        """
        if self.verbose:
            print(f"\033[36m  [📖 原生去重] 正在对比已有智库，剔除冗余...\033[0m")

        # ── Stage 1: Native Deduplication ─────────────────────────────
        # Convert to DeduplicationEngine format: (domain, rule_text, params)
        dedup_input = [(domain, rule_text, params) for domain, rule_text, params, _ in rules]
        accepted_rules, dedup_result = self.dedup_engine.deduplicate_rules(dedup_input)

        if self.verbose:
            print(
                f"\033[36m  [📖 去重完成] {dedup_result.summary()}\033[0m"
            )

        # Save dedup log
        if dedup_result.total_input > 0:
            self.dedup_engine.save_dedup_log(dedup_result, source=source_name)

        # ── Stage 2: Knowledge Triage ─────────────────────────────────
        if self.verbose:
            print(f"\033[36m  [⚖️ 知识分诊] 正在对通过去重的规则进行智能分流...\033[0m")

        # Rebuild rule_type mapping for accepted rules
        # Map accepted rules back to their original rule_type
        rule_type_map = {}
        for domain, rule_text, params, rule_type in rules:
            rule_type_map[(domain, rule_text)] = rule_type

        triage_input = []
        for domain, rule_text, params in accepted_rules:
            # Strip [VARIANT] prefix if present for triage
            clean_text = rule_text.replace("[VARIANT] ", "")
            rule_type = rule_type_map.get((domain, clean_text), "soft_default")
            triage_input.append((rule_text, params, rule_type))

        actionable_idx, macro_idx, triage_result = self.triage_engine.triage_batch(
            triage_input
        )

        # ── Separate into two streams ─────────────────────────────────
        actionable_rules = [accepted_rules[i] for i in actionable_idx]
        macro_rules = [accepted_rules[i] for i in macro_idx]

        return FunnelResult(
            dedup_result=dedup_result,
            triage_result=triage_result,
            actionable_rules=actionable_rules,
            macro_rules=macro_rules,
            source_name=source_name,
        )


@dataclass
class FunnelResult:
    """Complete result from the Knowledge Funnel."""
    dedup_result: DedupResult
    triage_result: TriageResult
    actionable_rules: list[tuple[str, str, dict]]  # (domain, rule_text, params)
    macro_rules: list[tuple[str, str, dict]]        # (domain, rule_text, params)
    source_name: str = ""

    def summary(self) -> str:
        return (
            f"FunnelResult[{self.source_name}]: "
            f"Dedup({self.dedup_result.summary()}) → "
            f"Triage({self.triage_result.summary()})"
        )

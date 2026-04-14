"""StagnationGuard — Invalid Iteration Detector and AI Arbitration Interface.

Design philosophy
-----------------
"Refuse invalid iteration" is a first-class concern. If the inner loop produces
an image that is visually identical to a previously rejected image (within a
configurable perceptual distance), the system must NOT silently continue.

Three escalation levels:
  1. AUTO-RECOVER: System can self-diagnose the cause (math-art conflict,
     parameter space exhaustion, etc.) and attempt a corrective action.
  2. AI-ARBITRATE: Cause is ambiguous; delegate to an LLM judge that returns
     a structured verdict with a recommended action.
  3. HUMAN-REQUIRED: LLM judge also fails or explicitly defers; generate a
     full diagnostic report for the user to review and feed back.

Stagnation detection algorithm
-------------------------------
A stagnation event is triggered when ALL of the following hold for N
consecutive iterations:
  - Score improvement < min_score_delta (default 0.005)
  - pHash Hamming distance between current best and previous best < phash_threshold
    (default 8 out of 64 bits, i.e., < 12.5% difference)

The second condition is the key "invalid iteration" guard: even if the score
fluctuates slightly, if the image LOOKS the same, it counts as stagnation.
"""
from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


# ── Enums ──────────────────────────────────────────────────────────────────────

class StagnationCause(str, Enum):
    """Diagnosed cause of stagnation."""
    MATH_ART_CONFLICT   = "math_art_conflict"    # Math constraints contradict art goals
    SPACE_EXHAUSTED     = "space_exhausted"       # Parameter space too narrow
    EVALUATOR_CEILING   = "evaluator_ceiling"     # Evaluator metric has a natural ceiling
    GENERATOR_INVARIANT = "generator_invariant"   # Generator ignores some parameters
    UNKNOWN             = "unknown"


class EscalationLevel(str, Enum):
    """Escalation level after stagnation."""
    AUTO_RECOVER    = "auto_recover"    # System fixed it automatically
    AI_ARBITRATE    = "ai_arbitrate"    # LLM judge called
    HUMAN_REQUIRED  = "human_required"  # Needs human decision


class ArbiterVerdict(str, Enum):
    """LLM arbitrator verdict."""
    CONTINUE_MODIFIED   = "continue_modified"   # Continue with modified strategy
    WIDEN_SPACE         = "widen_space"          # Widen parameter space
    CHANGE_GENERATOR    = "change_generator"     # Generator logic needs change
    STOP_REPORT         = "stop_report"          # Stop and report to human
    ADD_KNOWLEDGE       = "add_knowledge"        # Need more knowledge distillation


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class StagnationEvent:
    """Record of a detected stagnation event."""
    iteration:         int
    consecutive_count: int
    score_history:     list[float]
    phash_distances:   list[int]
    cause:             StagnationCause
    cause_explanation: str
    escalation:        EscalationLevel
    verdict:           Optional[ArbiterVerdict]
    verdict_reasoning: str
    recommended_action: str
    timestamp:         str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_report(self) -> str:
        """Render a human-readable stagnation report."""
        lines = [
            "=" * 70,
            "  STAGNATION REPORT",
            "=" * 70,
            f"  Detected at iteration : {self.iteration}",
            f"  Consecutive stagnant  : {self.consecutive_count}",
            f"  Score history (last 5): {self.score_history[-5:]}",
            f"  pHash distances       : {self.phash_distances[-5:]}",
            "",
            f"  Diagnosed cause       : {self.cause.value}",
            f"  Explanation           : {self.cause_explanation}",
            "",
            f"  Escalation level      : {self.escalation.value}",
            f"  Arbitrator verdict    : {self.verdict.value if self.verdict else 'N/A'}",
            f"  Verdict reasoning     : {self.verdict_reasoning}",
            "",
            f"  Recommended action    : {self.recommended_action}",
            "=" * 70,
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "iteration": self.iteration,
            "consecutive_count": self.consecutive_count,
            "score_history": self.score_history,
            "phash_distances": self.phash_distances,
            "cause": self.cause.value,
            "cause_explanation": self.cause_explanation,
            "escalation": self.escalation.value,
            "verdict": self.verdict.value if self.verdict else None,
            "verdict_reasoning": self.verdict_reasoning,
            "recommended_action": self.recommended_action,
            "timestamp": self.timestamp,
        }


@dataclass
class ArbiterResponse:
    """Structured response from the AI arbitrator."""
    verdict:            ArbiterVerdict
    reasoning:          str
    recommended_action: str
    modified_params:    dict = field(default_factory=dict)
    confidence:         float = 0.8


# ── Core StagnationGuard ───────────────────────────────────────────────────────

class StagnationGuard:
    """Detects invalid iterations and escalates appropriately.

    Parameters
    ----------
    patience : int
        Number of consecutive stagnant iterations before triggering.
    min_score_delta : float
        Minimum score improvement to be considered non-stagnant.
    phash_threshold : int
        Maximum Hamming distance (out of 64) to consider two images identical.
        Default 8 = 12.5% bit difference.
    use_llm : bool
        Whether to call the LLM arbitrator when auto-recovery fails.
    project_root : Path
        Project root for saving stagnation reports.
    verbose : bool
        Print progress messages.
    """

    def __init__(
        self,
        patience:         int   = 5,
        min_score_delta:  float = 0.005,
        phash_threshold:  int   = 8,
        use_llm:          bool  = True,
        project_root:     Optional[Path] = None,
        verbose:          bool  = False,
    ) -> None:
        self.patience         = patience
        self.min_score_delta  = min_score_delta
        self.phash_threshold  = phash_threshold
        self.use_llm          = use_llm
        self.project_root     = Path(project_root) if project_root else Path(".")
        self.verbose          = verbose

        self._score_history:  list[float] = []
        self._phash_history:  list[int]   = []
        self._image_history:  list[Optional[int]] = []  # pHash values
        self._consecutive:    int = 0
        self._prev_best_hash: Optional[int] = None
        self._prev_best_score: float = -1.0
        self._events:         list[StagnationEvent] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset guard state (call at the start of each inner loop run)."""
        self._score_history.clear()
        self._phash_history.clear()
        self._image_history.clear()
        self._consecutive = 0
        self._prev_best_hash = None
        self._prev_best_score = -1.0

    def update(
        self,
        iteration: int,
        score: float,
        image: Optional[Image.Image] = None,
        space_info: Optional[dict] = None,
    ) -> Optional[StagnationEvent]:
        """Feed a new iteration result into the guard.

        Parameters
        ----------
        iteration : int
            Current iteration number.
        score : float
            Quality score of the best individual in this generation.
        image : PIL.Image, optional
            Best image of this generation (for pHash comparison).
        space_info : dict, optional
            Info about the parameter space (for diagnosis).

        Returns
        -------
        StagnationEvent or None
            Returns an event if stagnation is detected, else None.
        """
        self._score_history.append(score)

        # Compute pHash distance
        current_hash = self._phash(image) if image is not None else None
        if current_hash is not None and self._prev_best_hash is not None:
            dist = bin(current_hash ^ self._prev_best_hash).count("1")
        else:
            dist = 64  # Unknown = treat as fully different

        self._phash_history.append(dist)

        # Check stagnation condition
        score_stagnant = abs(score - self._prev_best_score) < self.min_score_delta
        image_stagnant = dist < self.phash_threshold

        if score_stagnant and image_stagnant:
            self._consecutive += 1
        else:
            self._consecutive = 0

        # Update history
        if current_hash is not None:
            self._prev_best_hash = current_hash
        self._prev_best_score = score

        # Trigger if patience exceeded
        if self._consecutive >= self.patience:
            if self.verbose:
                print(f"[StagnationGuard] Stagnation detected at iteration {iteration} "
                      f"(consecutive={self._consecutive})")
            event = self._handle_stagnation(iteration, space_info or {})
            self._events.append(event)
            self._consecutive = 0  # Reset after handling
            return event

        return None

    def get_events(self) -> list[StagnationEvent]:
        """Return all stagnation events recorded so far."""
        return list(self._events)

    def save_report(self, event: StagnationEvent) -> Path:
        """Save a stagnation report to disk."""
        reports_dir = self.project_root / "stagnation_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filepath = reports_dir / f"stagnation_{ts}_iter{event.iteration}.json"
        filepath.write_text(
            json.dumps(event.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Also append to human-readable log
        log_path = self.project_root / "STAGNATION_LOG.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {ts} — Iteration {event.iteration}\n\n")
            f.write(f"```\n{event.to_report()}\n```\n")
        if self.verbose:
            print(f"[StagnationGuard] Report saved: {filepath}")
        return filepath

    # ── Internal logic ─────────────────────────────────────────────────────────

    def _handle_stagnation(
        self,
        iteration: int,
        space_info: dict,
    ) -> StagnationEvent:
        """Diagnose cause and escalate."""
        cause, explanation = self._diagnose(space_info)

        # Level 1: Try auto-recovery
        if cause in (StagnationCause.SPACE_EXHAUSTED, StagnationCause.EVALUATOR_CEILING):
            action = self._auto_recover(cause, space_info)
            event = StagnationEvent(
                iteration=iteration,
                consecutive_count=self._consecutive,
                score_history=list(self._score_history),
                phash_distances=list(self._phash_history),
                cause=cause,
                cause_explanation=explanation,
                escalation=EscalationLevel.AUTO_RECOVER,
                verdict=ArbiterVerdict.CONTINUE_MODIFIED,
                verdict_reasoning="Auto-recovery applied based on diagnosed cause.",
                recommended_action=action,
            )
            if self.verbose:
                print(f"[StagnationGuard] Auto-recover: {action}")
            return event

        # Level 2: LLM arbitration
        if self.use_llm:
            try:
                arbiter_resp = self._call_llm_arbitrator(cause, explanation, space_info)
                event = StagnationEvent(
                    iteration=iteration,
                    consecutive_count=self._consecutive,
                    score_history=list(self._score_history),
                    phash_distances=list(self._phash_history),
                    cause=cause,
                    cause_explanation=explanation,
                    escalation=EscalationLevel.AI_ARBITRATE,
                    verdict=arbiter_resp.verdict,
                    verdict_reasoning=arbiter_resp.reasoning,
                    recommended_action=arbiter_resp.recommended_action,
                )
                self.save_report(event)
                if self.verbose:
                    print(f"[StagnationGuard] AI verdict: {arbiter_resp.verdict.value}")
                return event
            except Exception as e:
                if self.verbose:
                    print(f"[StagnationGuard] LLM arbitration failed: {e}")

        # Level 3: Human required
        event = StagnationEvent(
            iteration=iteration,
            consecutive_count=self._consecutive,
            score_history=list(self._score_history),
            phash_distances=list(self._phash_history),
            cause=cause,
            cause_explanation=explanation,
            escalation=EscalationLevel.HUMAN_REQUIRED,
            verdict=ArbiterVerdict.STOP_REPORT,
            verdict_reasoning=(
                "Automated recovery and AI arbitration both failed or deferred. "
                "Human review required."
            ),
            recommended_action=(
                "Please review the stagnation report in STAGNATION_LOG.md, "
                "then feed back your decision via the project chat."
            ),
        )
        self.save_report(event)
        print("\n" + event.to_report())
        return event

    def _diagnose(self, space_info: dict) -> tuple[StagnationCause, str]:
        """Heuristic diagnosis of stagnation cause."""
        scores = self._score_history
        dists  = self._phash_history

        # All images look identical (pHash dist ≈ 0) → generator invariant
        if dists and max(dists[-self.patience:]) < 3:
            return (
                StagnationCause.GENERATOR_INVARIANT,
                "All generated images are perceptually identical (pHash distance < 3). "
                "The generator may be ignoring some parameters, or the parameter "
                "ranges are too narrow to produce visible variation.",
            )

        # Score plateaued near ceiling (> 0.85) → evaluator ceiling
        if scores and min(scores[-self.patience:]) > 0.85:
            return (
                StagnationCause.EVALUATOR_CEILING,
                f"Score plateaued at {scores[-1]:.3f} (above 0.85). "
                "The evaluator metrics may have reached their natural ceiling "
                "for this generator. Consider raising quality_threshold or "
                "accepting this result.",
            )

        # Parameter space is very narrow
        ranges = space_info.get("ranges", {})
        narrow = [
            k for k, (lo, hi) in ranges.items()
            if abs(hi - lo) < 0.1
        ]
        if len(narrow) >= len(ranges) // 2 and ranges:
            return (
                StagnationCause.SPACE_EXHAUSTED,
                f"Parameter space is very narrow: {narrow}. "
                "The optimizer has likely explored the entire space. "
                "Consider widening constraints or adding new parameters.",
            )

        # Score oscillating without improvement → math-art conflict
        if len(scores) >= 4:
            diffs = [abs(scores[i] - scores[i-1]) for i in range(1, len(scores))]
            recent = diffs[-self.patience:]
            if max(recent) > 0.02 and min(recent) < 0.005:
                return (
                    StagnationCause.MATH_ART_CONFLICT,
                    "Score is oscillating (alternating improvement and regression). "
                    "This typically indicates that math constraints are pulling "
                    "parameters in directions that conflict with art quality goals. "
                    "Example: strict anatomy ROM constraints may prevent the pose "
                    "that maximizes visual appeal.",
                )

        return (
            StagnationCause.UNKNOWN,
            "No clear cause identified from heuristics. "
            "Escalating to AI arbitrator for deeper analysis.",
        )

    def _auto_recover(self, cause: StagnationCause, space_info: dict) -> str:
        """Return a recovery action string for auto-recoverable causes."""
        if cause == StagnationCause.SPACE_EXHAUSTED:
            return (
                "AUTO-RECOVER: Widen all parameter ranges by 20% and restart "
                "the inner loop with a fresh population."
            )
        if cause == StagnationCause.EVALUATOR_CEILING:
            return (
                "AUTO-RECOVER: Score is already high (>0.85). "
                "Accepting current best result and stopping iteration."
            )
        return "AUTO-RECOVER: Applying default recovery strategy."

    def _call_llm_arbitrator(
        self,
        cause: StagnationCause,
        explanation: str,
        space_info: dict,
    ) -> ArbiterResponse:
        """Call the LLM arbitrator for a structured verdict."""
        try:
            from openai import OpenAI
            client = OpenAI()
        except ImportError:
            raise RuntimeError("openai package not installed")

        prompt = textwrap.dedent(f"""
            You are an expert in procedural pixel art generation and mathematical
            optimization. A self-evolving art pipeline has detected stagnation.

            STAGNATION CONTEXT:
            - Diagnosed cause: {cause.value}
            - Explanation: {explanation}
            - Score history (last 10): {self._score_history[-10:]}
            - pHash distances (last 10): {self._phash_history[-10:]}
            - Parameter space info: {json.dumps(space_info, indent=2)}

            Please provide a structured verdict in JSON format:
            {{
              "verdict": "<one of: continue_modified, widen_space, change_generator, stop_report, add_knowledge>",
              "reasoning": "<2-3 sentence explanation>",
              "recommended_action": "<specific actionable step>",
              "confidence": <0.0-1.0>
            }}

            Guidelines:
            - Use "continue_modified" if you can suggest a specific parameter adjustment.
            - Use "widen_space" if the parameter ranges are clearly too narrow.
            - Use "change_generator" if the generator logic itself needs modification.
            - Use "add_knowledge" if more domain knowledge (anatomy, physics, etc.) would help.
            - Use "stop_report" only if the situation requires human judgment.
        """).strip()

        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()

        # Parse JSON from response
        try:
            # Extract JSON block if wrapped in markdown
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Fallback: extract verdict keyword
            verdict_str = "stop_report"
            for v in ArbiterVerdict:
                if v.value in raw.lower():
                    verdict_str = v.value
                    break
            data = {
                "verdict": verdict_str,
                "reasoning": raw[:200],
                "recommended_action": "Review LLM response manually.",
                "confidence": 0.5,
            }

        return ArbiterResponse(
            verdict=ArbiterVerdict(data.get("verdict", "stop_report")),
            reasoning=data.get("reasoning", ""),
            recommended_action=data.get("recommended_action", ""),
            confidence=float(data.get("confidence", 0.5)),
        )

    # ── Static helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _phash(image: Image.Image, hash_size: int = 8) -> int:
        """Compute a 64-bit perceptual hash (pHash) of an image."""
        # Resize to hash_size x hash_size for fast comparison
        img = image.convert("L").resize(
            (hash_size, hash_size), Image.LANCZOS
        )
        pixels = np.array(img, dtype=float)
        # Mean-based hash: compare each pixel to the mean
        mean_val = pixels.mean()
        bits = (pixels > mean_val).flatten()
        # Pack into integer
        h = 0
        for bit in bits:
            h = (h << 1) | int(bit)
        return h

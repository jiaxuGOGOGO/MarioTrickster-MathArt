"""Quality checkpoint data structures."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PIL import Image


class CheckpointStage(str, Enum):
    PRE_GENERATION  = "pre_generation"
    MID_GENERATION  = "mid_generation"
    POST_GENERATION = "post_generation"
    ITERATION_END   = "iteration_end"


class CheckpointDecision(str, Enum):
    CONTINUE    = "continue"       # All good, proceed
    ADJUST      = "adjust"         # Minor adjustments needed, continue
    RETRY       = "retry"          # Discard this result, retry with new params
    ESCALATE    = "escalate"       # Stagnation detected, escalate
    STOP        = "stop"           # Quality target reached, stop iterating


@dataclass
class KnowledgeViolation:
    """A violation of a distilled knowledge rule."""
    rule_id:     str
    rule_text:   str
    severity:    float      # 0-1: how badly violated
    suggestion:  str        # How to fix it


@dataclass
class MathViolation:
    """A violation of a mathematical model constraint."""
    model_name:  str
    param_name:  str
    actual:      float
    expected_lo: float
    expected_hi: float
    severity:    float

    @property
    def suggestion(self) -> str:
        return (
            f"Adjust {self.param_name} from {self.actual:.3f} "
            f"to range [{self.expected_lo:.3f}, {self.expected_hi:.3f}]"
        )


@dataclass
class CheckpointResult:
    """Result of a quality checkpoint evaluation."""
    stage:               CheckpointStage
    iteration:           int
    decision:            CheckpointDecision
    quality_score:       float                         # 0-1 overall
    knowledge_score:     float                         # 0-1 knowledge compliance
    math_score:          float                         # 0-1 math model compliance
    sprite_ref_score:    float                         # 0-1 similarity to best reference
    knowledge_violations: list[KnowledgeViolation]    = field(default_factory=list)
    math_violations:      list[MathViolation]         = field(default_factory=list)
    param_adjustments:    dict[str, float]            = field(default_factory=dict)
    message:             str                          = ""
    image:               Optional[Image.Image]        = field(default=None, repr=False)

    @property
    def combined_score(self) -> float:
        """Weighted combination: quality 50%, knowledge 30%, math 20%."""
        return (
            self.quality_score    * 0.50 +
            self.knowledge_score  * 0.30 +
            self.math_score       * 0.20
        )

    def summary(self) -> str:
        lines = [
            f"[{self.stage.value}] iter={self.iteration} "
            f"decision={self.decision.value}",
            f"  Scores: quality={self.quality_score:.3f} "
            f"knowledge={self.knowledge_score:.3f} "
            f"math={self.math_score:.3f} "
            f"ref={self.sprite_ref_score:.3f} "
            f"→ combined={self.combined_score:.3f}",
        ]
        if self.knowledge_violations:
            lines.append(f"  Knowledge violations: {len(self.knowledge_violations)}")
            for v in self.knowledge_violations[:3]:
                lines.append(f"    • {v.rule_text[:60]} (severity={v.severity:.2f})")
        if self.math_violations:
            lines.append(f"  Math violations: {len(self.math_violations)}")
            for v in self.math_violations[:3]:
                lines.append(f"    • {v.suggestion}")
        if self.param_adjustments:
            lines.append(f"  Param adjustments: {self.param_adjustments}")
        if self.message:
            lines.append(f"  Message: {self.message}")
        return "\n".join(lines)


class QualityCheckpoint:
    """Lightweight wrapper for running a single checkpoint evaluation.

    Used by the pipeline stages to record and log checkpoint results.
    """

    def __init__(self, stage: CheckpointStage, iteration: int) -> None:
        self.stage     = stage
        self.iteration = iteration
        self._results: list[CheckpointResult] = []

    def record(self, result: CheckpointResult) -> None:
        self._results.append(result)

    def latest(self) -> Optional[CheckpointResult]:
        return self._results[-1] if self._results else None

    def history(self) -> list[CheckpointResult]:
        return list(self._results)

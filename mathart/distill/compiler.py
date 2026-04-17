"""Compilation Layer: Transform rules into mathematical constraints.

This module takes structured ``KnowledgeRule`` objects from the parser and
compiles them into ``ParameterSpace`` definitions that the optimization layer
can search. It bridges the gap between human-readable knowledge and
machine-optimizable parameter configurations.

The compiler maintains a registry of known parameters and their relationships,
enabling automatic constraint propagation when new rules are added.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union

from .parser import KnowledgeRule, RuleType, TargetModule


@dataclass
class Constraint:
    """A mathematical constraint on a parameter.

    Attributes
    ----------
    param_name : str
        Fully qualified parameter name (e.g., "animation.skeleton.shoulder_rom").
    min_value : float or None
        Minimum allowed value.
    max_value : float or None
        Maximum allowed value.
    default_value : float or None
        Recommended default.
    allowed_values : list or None
        For enum-type constraints.
    formula : str or None
        Mathematical relationship (e.g., "damping = mass * 0.3").
    is_hard : bool
        Whether this is a hard constraint (must not be violated).
    source_rule_id : str
        ID of the KnowledgeRule that generated this constraint.
    """

    param_name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    default_value: Optional[float] = None
    allowed_values: Optional[list] = None
    formula: Optional[str] = None
    is_hard: bool = False
    source_rule_id: str = ""

    def contains(self, value: float) -> bool:
        """Check if a value satisfies this constraint."""
        if self.allowed_values is not None:
            return value in self.allowed_values
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Constraint":
        return cls(**d)


@dataclass
class ParameterSpace:
    """A searchable parameter space defined by compiled constraints.

    This represents the "search space" that the optimization layer explores.
    Each dimension corresponds to a parameter with its valid range.
    """

    name: str
    constraints: dict[str, Constraint] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def add_constraint(self, constraint: Constraint) -> None:
        """Add or merge a constraint into the space."""
        key = constraint.param_name
        if key in self.constraints:
            existing = self.constraints[key]
            # Merge: tighten ranges
            if constraint.min_value is not None:
                if existing.min_value is None:
                    existing.min_value = constraint.min_value
                else:
                    existing.min_value = max(existing.min_value, constraint.min_value)
            if constraint.max_value is not None:
                if existing.max_value is None:
                    existing.max_value = constraint.max_value
                else:
                    existing.max_value = min(existing.max_value, constraint.max_value)
            if constraint.default_value is not None:
                existing.default_value = constraint.default_value
            if constraint.is_hard:
                existing.is_hard = True
        else:
            self.constraints[key] = constraint

    def get_ranges(self) -> dict[str, tuple[float, float]]:
        """Return parameter ranges as {name: (min, max)} dict."""
        ranges = {}
        for name, c in self.constraints.items():
            lo = c.min_value if c.min_value is not None else 0.0
            hi = c.max_value if c.max_value is not None else 1.0
            ranges[name] = (lo, hi)
        return ranges

    def get_defaults(self) -> dict[str, float]:
        """Return default values for all parameters."""
        defaults = {}
        for name, c in self.constraints.items():
            if c.default_value is not None:
                defaults[name] = c.default_value
            elif c.min_value is not None and c.max_value is not None:
                defaults[name] = (c.min_value + c.max_value) / 2.0
            elif c.min_value is not None:
                defaults[name] = c.min_value
            elif c.max_value is not None:
                defaults[name] = c.max_value
            else:
                defaults[name] = 0.0
        return defaults

    def validate(self, params: dict[str, float]) -> list[str]:
        """Validate a parameter set against all constraints.

        Returns a list of violation messages (empty if valid).
        """
        violations = []
        for name, value in params.items():
            if name in self.constraints:
                c = self.constraints[name]
                if not c.contains(value):
                    violations.append(
                        f"{name}={value} violates constraint "
                        f"[{c.min_value}, {c.max_value}] "
                        f"(hard={c.is_hard}, rule={c.source_rule_id})"
                    )
        return violations

    @property
    def dimensions(self) -> int:
        """Number of parameters in the space."""
        return len(self.constraints)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "constraints": {k: v.to_dict() for k, v in self.constraints.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParameterSpace":
        space = cls(name=d["name"], metadata=d.get("metadata", {}))
        for k, v in d.get("constraints", {}).items():
            space.constraints[k] = Constraint.from_dict(v)
        return space


# ── Parameter name mapping ────────────────────────────────────────────

# Maps common knowledge terms to fully qualified parameter names
_PARAM_MAPPING: dict[str, str] = {
    # Animation / Skeleton
    "肩关节": "animation.skeleton.shoulder_rom",
    "肘关节": "animation.skeleton.elbow_rom",
    "髋关节": "animation.skeleton.hip_rom",
    "膝关节": "animation.skeleton.knee_rom",
    "头身比": "animation.skeleton.head_ratio",
    "shoulder": "animation.skeleton.shoulder_rom",
    "elbow": "animation.skeleton.elbow_rom",
    "hip": "animation.skeleton.hip_rom",
    "knee": "animation.skeleton.knee_rom",
    "head_ratio": "animation.skeleton.head_ratio",
    # Animation / Dynamics
    "弹簧刚度": "animation.spring.stiffness",
    "阻尼系数": "animation.spring.damping",
    "质量": "animation.spring.mass",
    "stiffness": "animation.spring.stiffness",
    "damping": "animation.spring.damping",
    "mass": "animation.spring.mass",
    # Animation / Timing
    "帧数": "animation.timing.frame_count",
    "帧率": "animation.timing.fps",
    "frame_count": "animation.timing.frame_count",
    "fps": "animation.timing.fps",
    # Animation / Timing (PROMPT_RECIPES distillation)
    "コマ打ち": "animation.timing.koma_uchi",
    "予備動作比率": "animation.anticipation.ratio",
    "フォロースルー比率": "animation.followthrough.ratio",
    "オーバーシュート": "animation.overshoot.ratio",
    "最大拉伸": "animation.squash_stretch.max_stretch",
    "最大挤压": "animation.squash_stretch.max_squash",
    "歩行周期帧数": "animation.walk.frame_count",
    "接地相比率": "animation.walk.contact_ratio",
    "腕振り角度": "animation.walk.arm_swing_deg",
    "オーバーサイズ倍率": "animation.oversize.ratio",
    "koma_uchi": "animation.timing.koma_uchi",
    "anticipation_ratio": "animation.anticipation.ratio",
    "followthrough_ratio": "animation.followthrough.ratio",
    "overshoot": "animation.overshoot.ratio",
    "max_stretch": "animation.squash_stretch.max_stretch",
    "max_squash": "animation.squash_stretch.max_squash",
    "walk_frames": "animation.walk.frame_count",
    "contact_ratio": "animation.walk.contact_ratio",
    "arm_swing": "animation.walk.arm_swing_deg",
    "oversize_ratio": "animation.oversize.ratio",
    # OKLAB / Palette
    "色相偏移": "oklab.palette.hue_shift",
    "饱和度": "oklab.palette.saturation",
    "明度范围": "oklab.palette.lightness_range",
    "hue_shift": "oklab.palette.hue_shift",
    "saturation": "oklab.palette.saturation",
    # Export / Unity
    "PPU": "export.unity.ppu",
    "ppu": "export.unity.ppu",
    "filter_mode": "export.unity.filter_mode",
    # Level / WFC
    "关卡宽度": "level.wfc.width",
    "关卡高度": "level.wfc.height",
    "level_width": "level.wfc.width",
    "level_height": "level.wfc.height",
    "max_gap": "level.wfc.max_gap",
    "max_height": "level.wfc.max_height",
    "safe_gap": "level.wfc.safe_gap",
    "danger_gap": "level.wfc.danger_gap",
    "secret_ratio": "level.wfc.secret_ratio",
    "最大跳跃距离": "level.wfc.max_gap",
    "最大跳跃高度": "level.wfc.max_height",
    "安全间距": "level.wfc.safe_gap",
    "秘密区域比例": "level.wfc.secret_ratio",
    # Level / Difficulty
    "难度增长率": "level.difficulty.growth_rate",
    "休息关比例": "level.difficulty.rest_ratio",
    "紧张段长度": "level.rhythm.tension_length",
    "放松段长度": "level.rhythm.relax_length",
    "紧张/放松比": "level.rhythm.tension_relax_ratio",
    # Game Design
    "核心动词数量": "game_design.core_loop.verb_count",
    "反馈延迟": "game_design.core_loop.feedback_delay_ms",
    "挑战/技能比": "game_design.flow.challenge_skill_ratio",
    "心流持续时间": "game_design.flow.duration_s",
    "系统交互数": "game_design.multiplicative.interaction_count",
    "涌现比": "game_design.multiplicative.emergence_ratio",
    # Game Feel
    "输入延迟": "game_feel.input.delay_frames",
    "缓冲窗口": "game_feel.input.buffer_frames",
    "土狼时间": "game_feel.input.coyote_frames",
    "加速时间": "game_feel.motion.accel_time",
    "减速时间": "game_feel.motion.decel_time",
    "空中控制力": "game_feel.motion.air_control",
    "顿帧时长": "game_feel.hit.hitstop_frames",
    "击退距离": "game_feel.hit.knockback_tiles",
    "闪白帧数": "game_feel.hit.flash_frames",
    "震动幅度": "game_feel.hit.shake_px",
    "hitstop": "game_feel.hit.hitstop_frames",
    "knockback": "game_feel.hit.knockback_tiles",
    "coyote_time": "game_feel.input.coyote_frames",
    "buffer_window": "game_feel.input.buffer_frames",
    # Pixel Art
    "画布尺寸": "pixel_art.canvas.size_px",
    "调色板大小": "pixel_art.palette.color_count",
    "抖动矩阵大小": "pixel_art.dither.matrix_size",
    "抖动强度": "pixel_art.dither.strength",
    "子像素帧数": "pixel_art.subpixel.frame_count",
    "锯齿容忍度": "pixel_art.line.jagged_tolerance",
    "canvas_size": "pixel_art.canvas.size_px",
    "palette_size": "pixel_art.palette.color_count",
    "dither_strength": "pixel_art.dither.strength",
    # Physics Simulation
    "弹簧刚度 k": "physics.spring.stiffness",
    "阻尼系数 c": "physics.spring.damping",
    "质量 m": "physics.spring.mass",
    "恢复系数": "physics.collision.restitution",
    "摩擦系数": "physics.collision.friction",
    "节点数": "physics.cloth.node_count",
    "约束迭代次数": "physics.cloth.constraint_iters",
    "松弛系数": "physics.cloth.relaxation",
    "spring_k": "physics.spring.stiffness",
    "damping_c": "physics.spring.damping",
    "restitution": "physics.collision.restitution",
    "friction": "physics.collision.friction",
    "脚接触高度阈值": "physics.contact.height_threshold",
    "脚接触速度阈值": "physics.contact.velocity_threshold",
    "脚锁定最小权重": "physics.foot_lock.min_blend_weight",
    "脚锁定混入帧数": "physics.constraint.blend_in_frames",
    "脚锁定混出帧数": "physics.constraint.blend_out_frames",
    "foot_contact_height": "physics.contact.height_threshold",
    "foot_contact_velocity": "physics.contact.velocity_threshold",
    "contact_height": "physics.contact.height_threshold",
    "contact_velocity": "physics.contact.velocity_threshold",
    "foot_lock_blend": "physics.foot_lock.min_blend_weight",
    "blend_in_frames": "physics.constraint.blend_in_frames",
    "blend_out_frames": "physics.constraint.blend_out_frames",
    # Plant / L-System
    "分支角度": "lsystem.branch.angle",
    "长度衰减比": "lsystem.branch.length_ratio",
    "粗度衰减比": "lsystem.branch.width_ratio",
    "最大递归深度": "lsystem.branch.max_depth",
    "黄金角": "lsystem.phyllotaxis.golden_angle",
    "branch_angle": "lsystem.branch.angle",
    "length_ratio": "lsystem.branch.length_ratio",
    "width_ratio": "lsystem.branch.width_ratio",
    "max_depth": "lsystem.branch.max_depth",
    # VFX
    "粒子生命周期": "vfx.particle.lifetime",
    "发射速率": "vfx.particle.emit_rate",
    "初始速度范围": "vfx.particle.initial_velocity",
    "重力影响": "vfx.particle.gravity_scale",
    "震动频率": "vfx.screenshake.frequency",
    "衰减时间": "vfx.screenshake.decay_time",
    "particle_lifetime": "vfx.particle.lifetime",
    "emit_rate": "vfx.particle.emit_rate",
    # VFX / Effects (PROMPT_RECIPES distillation)
    "爆発花弁数": "vfx.explosion.petal_count",
    "爆発中心空洞": "vfx.explosion.hollow_ratio",
    "雷ジグザグ角度": "vfx.lightning.zigzag_angle",
    "雷分岐確率": "vfx.lightning.branch_prob",
    "ビーム幅": "vfx.beam.width",
    "ビームリング数": "vfx.beam.ring_count",
    "水滴数": "vfx.splash.droplet_count",
    "波紋同心円数": "vfx.splash.ripple_count",
    "集中線密度": "vfx.speed_lines.count",
    "集中線太さ": "vfx.speed_lines.width",
    "エフェクト開始遅延": "vfx.timing.start_delay",
    "エフェクトピーク": "vfx.timing.peak_ratio",
    "VFXループ帧数": "vfx.loop.frame_count",
    "explosion_petals": "vfx.explosion.petal_count",
    "lightning_zigzag": "vfx.lightning.zigzag_angle",
    "lightning_branch_prob": "vfx.lightning.branch_prob",
    "beam_width": "vfx.beam.width",
    "beam_rings": "vfx.beam.ring_count",
    "splash_count": "vfx.splash.droplet_count",
    "ripple_count": "vfx.splash.ripple_count",
    "speed_line_count": "vfx.speed_lines.count",
    "speed_line_width": "vfx.speed_lines.width",
    "effect_delay": "vfx.timing.start_delay",
    "effect_peak": "vfx.timing.peak_ratio",
    "vfx_loop_frames": "vfx.loop.frame_count",
    # SDF
    "轮廓线颜色数": "sdf.outline.color_layers",
    "outline_colors": "sdf.outline.color_layers",
    # Programming / Architecture
    "状态数量": "programming.fsm.state_count",
    "混合时间": "programming.fsm.blend_frames",
    "state_count": "programming.fsm.state_count",
    "blend_frames": "programming.fsm.blend_frames",
    "对象池大小": "programming.pool.size",
    "事件队列长度": "programming.event.queue_length",
    "pool_size": "programming.pool.size",
    # Level Design (spatial / pacing)
    "平台最小宽度": "level_design.platform.min_width",
    "平台最大宽度": "level_design.platform.max_width",
    "安全区域比例": "level_design.pacing.safe_ratio",
    "危险区域比例": "level_design.pacing.danger_ratio",
    "秘密区域比例": "level_design.pacing.secret_ratio",
    "视觉引导强度": "level_design.visual.guide_strength",
    "四步教学法比例": "level_design.kishoten.ratio",
    "platform_min_w": "level_design.platform.min_width",
    "platform_max_w": "level_design.platform.max_width",
    "safe_ratio": "level_design.pacing.safe_ratio",
    "danger_ratio": "level_design.pacing.danger_ratio",
    # Perspective
    "消失点数量": "perspective.vanishing_point.count",
    "视平线高度": "perspective.horizon.height_ratio",
    "前缩率": "perspective.foreshortening.ratio",
    "深度层数": "perspective.depth.layer_count",
    "vanishing_points": "perspective.vanishing_point.count",
    "horizon_height": "perspective.horizon.height_ratio",
    # Perspective (PROMPT_RECIPES distillation)
    "VP主体距離": "perspective.vp_safety.min_distance",
    "2VP間距離": "perspective.vp_safety.min_vp_distance",
    "標準画角": "perspective.fov.default_mm",
    "空気遠近彩度減衰": "perspective.aerial.desaturate_rate",
    "近大遠小比": "perspective.foreshortening.near_far_ratio",
    "vp_min_distance": "perspective.vp_safety.min_distance",
    "vp_pair_distance": "perspective.vp_safety.min_vp_distance",
    "default_fov": "perspective.fov.default_mm",
    "aerial_desaturate": "perspective.aerial.desaturate_rate",
    "near_far_ratio": "perspective.foreshortening.near_far_ratio",
    # Anatomy
    "头身比": "anatomy.proportion.head_ratio",
    "手臂比": "anatomy.proportion.arm_ratio",
    "腰臀比": "anatomy.proportion.waist_hip_ratio",
    "肩宽比": "anatomy.proportion.shoulder_ratio",
    "head_body_ratio": "anatomy.proportion.head_ratio",
    "arm_ratio": "anatomy.proportion.arm_ratio",
    # Anatomy (PROMPT_RECIPES distillation)
    "手腕ROM": "anatomy.joint.wrist_rom",
    "足首ROM": "anatomy.joint.ankle_rom",
    "顔三分割": "anatomy.face.thirds_ratio",
    "目の位置": "anatomy.face.eye_position",
    "手のサイズ": "anatomy.hand.size_ratio",
    "wrist_rom": "anatomy.joint.wrist_rom",
    "ankle_rom": "anatomy.joint.ankle_rom",
    "face_thirds": "anatomy.face.thirds_ratio",
    "eye_position": "anatomy.face.eye_position",
    "hand_size": "anatomy.hand.size_ratio",
    # PBR / Lighting
    "金属度": "pbr.material.metallic",
    "粗糙度": "pbr.material.roughness",
    "AO强度": "pbr.material.ao_strength",
    "法线强度": "pbr.normal.strength",
    "metallic": "pbr.material.metallic",
    "roughness": "pbr.material.roughness",
    "ao_strength": "pbr.material.ao_strength",
    "normal_strength": "pbr.normal.strength",
}


class RuleCompiler:
    """Compiles KnowledgeRules into ParameterSpaces.

    Usage::

        compiler = RuleCompiler()
        space = compiler.compile(rules, "animation_params")
        print(space.get_ranges())
    """

    def __init__(self, param_mapping: Optional[dict[str, str]] = None):
        self.param_mapping = param_mapping or _PARAM_MAPPING

    def compile(
        self, rules: list[KnowledgeRule], space_name: str = "default"
    ) -> ParameterSpace:
        """Compile a list of rules into a parameter space."""
        space = ParameterSpace(name=space_name)

        for rule in rules:
            constraint = self._rule_to_constraint(rule)
            if constraint is not None:
                space.add_constraint(constraint)

        space.metadata = {
            "compiled_from": len(rules),
            "dimensions": space.dimensions,
        }
        return space

    def compile_by_module(
        self, rules: list[KnowledgeRule]
    ) -> dict[str, ParameterSpace]:
        """Compile rules into separate parameter spaces per module."""
        by_module: dict[str, list[KnowledgeRule]] = {}
        for rule in rules:
            mod = rule.target_module.value
            by_module.setdefault(mod, []).append(rule)

        spaces = {}
        for mod_name, mod_rules in by_module.items():
            spaces[mod_name] = self.compile(mod_rules, f"{mod_name}_params")
        return spaces

    def _rule_to_constraint(self, rule: KnowledgeRule) -> Optional[Constraint]:
        """Convert a single rule to a constraint."""
        constraint_data = rule.constraint
        if not constraint_data:
            return None

        # Resolve parameter name
        param_name = self._resolve_param_name(rule.target_param, rule.target_module)

        ctype = constraint_data.get("type", "")

        if ctype == "range":
            return Constraint(
                param_name=param_name,
                min_value=constraint_data.get("min"),
                max_value=constraint_data.get("max"),
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "exact":
            val = constraint_data.get("value")
            return Constraint(
                param_name=param_name,
                default_value=val,
                min_value=val,
                max_value=val,
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "enum":
            return Constraint(
                param_name=param_name,
                allowed_values=constraint_data.get("values"),
                is_hard=rule.rule_type == RuleType.HARD_CONSTRAINT,
                source_rule_id=rule.id,
            )
        elif ctype == "formula":
            return Constraint(
                param_name=param_name,
                formula=constraint_data.get("expr"),
                is_hard=False,
                source_rule_id=rule.id,
            )

        return None

    def _resolve_param_name(self, raw_name: str, module: TargetModule) -> str:
        """Resolve a raw parameter name to a fully qualified name."""
        raw_clean = raw_name.strip()

        # Direct lookup
        if raw_clean in self.param_mapping:
            return self.param_mapping[raw_clean]

        # Case-insensitive lookup
        for key, value in self.param_mapping.items():
            if key.lower() == raw_clean.lower():
                return value

        # Fallback: construct from module + raw name
        safe_name = re.sub(r"[^\w]", "_", raw_clean).lower().strip("_")
        return f"{module.value}.{safe_name}"

    # ── Serialization ────────────────────────────────────────────────

    @staticmethod
    def save_space(space: ParameterSpace, filepath: Union[str, Path]) -> None:
        """Save a parameter space to JSON."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(
            json.dumps(space.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def load_space(filepath: Union[str, Path]) -> ParameterSpace:
        """Load a parameter space from JSON."""
        filepath = Path(filepath)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return ParameterSpace.from_dict(data)


# Need re for _resolve_param_name fallback
import re

"""Perception Layer: Knowledge extraction and structuring.

This module parses knowledge sources (Markdown files, plain text, and PDF
documents) into structured ``KnowledgeRule`` objects. Each rule captures:

- A human-readable description of the artistic/design principle.
- The affected module(s) and parameter(s).
- Quantitative constraints (ranges, enums, formulas).
- Source attribution (book, author, page).

The parser understands the table-based format used in ``knowledge/*.md`` files
and can also extract rules from free-form text via pattern matching.

Supported knowledge domains (not limited to art):
- Art fundamentals: anatomy, perspective, color/light, pixel art, VFX
- Animation: skeletal, procedural, physics simulation
- Game design: mechanics, level design, narrative, difficulty curves
- Technical: PBR/shaders, procedural generation, math/physics
- Production: Unity integration, asset pipeline, naming conventions
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Union


class RuleType(str, Enum):
    """Classification of how a rule should be applied."""

    HARD_CONSTRAINT = "hard_constraint"     # Must be enforced as code constant
    SOFT_DEFAULT = "soft_default"           # Default parameter, overridable
    HEURISTIC = "heuristic"                 # Guidance for manual tuning


class TargetModule(str, Enum):
    """Modules that can be affected by distilled knowledge.

    This enum covers all current and planned code modules, plus a GENERAL
    catch-all for cross-domain knowledge that doesn't map to a single module.
    New modules can be added as the project evolves.
    """

    # ── Core rendering ──
    ANIMATION = "animation"       # Skeletal animation, FK/IK, curves, presets
    OKLAB = "oklab"               # Color science, palette generation, quantization
    SDF = "sdf"                   # Signed distance fields, effects, L-system plants
    EXPORT = "export"             # Unity bridge, asset validation, naming

    # ── Procedural generation ──
    LEVEL = "level"               # WFC level generation, tile constraints
    LSYSTEM = "lsystem"          # L-system plant grammar (subset of SDF)

    # ── Game design & feel ──
    GAME_DESIGN = "game_design"   # Mechanics, difficulty curves, systems design
    LEVEL_DESIGN = "level_design" # Spatial language, platform constraints, pacing
    GAME_FEEL = "game_feel"       # Input buffering, motion curves, hit-stop, juice

    # ── Art & visual ──
    PIXEL_ART = "pixel_art"       # Pixel art techniques, dithering, sub-pixel
    PBR = "pbr"                   # PBR lighting, normal maps, BRDF
    VFX = "vfx"                   # Particle systems, visual effects
    PERSPECTIVE = "perspective"   # Perspective rules, depth cues, projection
    ANATOMY = "anatomy"           # Human anatomy, proportions, joint ROM

    # ── Technical ──
    PHYSICS = "physics"           # Spring-damper, collision, rigid body, cloth
    PROGRAMMING = "programming"   # FSM, data-driven, PCG architecture, patterns
    SHADER = "shader"             # Shader math, GPU techniques
    NARRATIVE = "narrative"       # Story structure, pacing, world building

    # ── Catch-all ──
    GENERAL = "general"           # Cross-domain or uncategorized knowledge


@dataclass
class KnowledgeRule:
    """A single distilled knowledge rule.

    Attributes
    ----------
    id : str
        Unique identifier (e.g., "anat_001").
    description : str
        Human-readable description of the rule.
    rule_type : RuleType
        How the rule should be applied.
    target_module : TargetModule
        Which code module this rule affects.
    target_param : str
        Specific parameter or function affected.
    constraint : dict
        Quantitative constraint. Examples:
        - {"type": "range", "min": 0, "max": 180}
        - {"type": "enum", "values": ["ease", "spring"]}
        - {"type": "formula", "expr": "mass * 0.3 + 0.1"}
    source : str
        Attribution (book/author/page).
    tags : list[str]
        Searchable tags.
    """

    id: str
    description: str
    rule_type: RuleType
    target_module: TargetModule
    target_param: str
    constraint: dict = field(default_factory=dict)
    source: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rule_type"] = self.rule_type.value
        d["target_module"] = self.target_module.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeRule":
        d["rule_type"] = RuleType(d["rule_type"])
        d["target_module"] = TargetModule(d["target_module"])
        return cls(**d)


class KnowledgeParser:
    """Parses knowledge sources into structured rules.

    Supports:
    - Markdown files with table-based rules (``knowledge/*.md`` format).
    - JSON rule files (for machine-generated rules).
    - Plain text with pattern-based extraction.

    The parser automatically detects the target module based on keyword
    matching across 16 knowledge domains. It is designed to absorb
    knowledge from ANY type of source — not limited to art books.

    Usage::

        parser = KnowledgeParser()
        rules = parser.parse_directory("knowledge/")
        parser.save_rules(rules, "rules.json")
    """

    # Regex patterns for extracting structured info from Markdown tables
    _TABLE_ROW_RE = re.compile(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|")
    _RANGE_RE = re.compile(r"(-?\d+\.?\d*)\s*[-–~]\s*(-?\d+\.?\d*)")
    _SOURCE_RE = re.compile(r"来源[：:]\s*(.+?)(?:\n|$)", re.IGNORECASE)
    _SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

    # ── Module keyword mapping ──────────────────────────────────────
    # Each module has Chinese + English keywords for broad matching.
    # When a PDF is distilled, the section text is matched against these
    # keywords to auto-route knowledge to the correct module.
    _MODULE_KEYWORDS = {
        TargetModule.ANIMATION: [
            "动画", "帧", "运动", "缓动", "关键帧", "时间轴", "循环",
            "animation", "frame", "motion", "easing", "keyframe", "timeline",
            "squash", "stretch", "anticipation", "follow-through",
            "12法则", "twelve principles",
        ],
        TargetModule.ANATOMY: [
            "关节", "骨骼", "肌肉", "人体", "头身比", "解剖", "ROM",
            "joint", "skeleton", "muscle", "anatomy", "proportion",
            "torso", "limb", "松岡", "伯里曼",
        ],
        TargetModule.OKLAB: [
            "色彩", "调色", "色相", "饱和", "明度", "暖光", "冷影",
            "配色", "色阶", "色温", "互补", "邻近色", "三角配色",
            "color", "palette", "hue", "saturation", "lightness",
            "OKLAB", "OKLCH", "gamut", "harmony",
        ],
        TargetModule.SDF: [
            "SDF", "距离场", "形状", "轮廓", "布尔运算",
            "signed distance", "shape", "outline", "boolean",
            "smooth union", "smooth subtraction",
        ],
        TargetModule.EXPORT: [
            "PPU", "Unity", "导出", "pivot", "filter", "资产",
            "export", "sprite", "atlas", "import settings",
            "命名规范", "目录结构", "naming convention",
        ],
        TargetModule.LEVEL: [
            "关卡", "地形", "布局", "WFC", "波函数坍缩", "地图",
            "level", "terrain", "layout", "tile", "tilemap",
            "wave function collapse", "adjacency",
        ],
        TargetModule.LSYSTEM: [
            "L-系统", "L-system", "植物", "分形", "文法",
            "plant", "fractal", "grammar", "branching",
            "树", "藤蔓", "蕨类", "tree", "vine", "fern",
        ],
        TargetModule.GAME_DESIGN: [
            "游戏设计", "机制", "系统设计", "难度曲线", "心流",
            "反馈循环", "奖励", "惩罚", "玩家体验", "交互",
            "涌现", "乘法式", "game design", "mechanics", "systems",
            "difficulty", "flow", "feedback loop", "reward",
            "emergence", "multiplicative",
            "MDA", "游戏循环", "core loop",
        ],
        TargetModule.LEVEL_DESIGN: [
            "关卡设计", "空间语言", "平台跳跃", "四步教学法",
            "安全区域", "危险区域", "秘密区域", "节奏",
            "紧张", "放松", "引导线", "视觉引导",
            "level design", "spatial language", "platform",
            "kishōtenketsu", "safe zone", "danger zone",
            "pacing", "tension", "release", "breadcrumb",
            "任天堂", "Nintendo", "Miyamoto",
        ],
        TargetModule.GAME_FEEL: [
            "手感", "打击感", "顿帧", "击退", "屏幕震动",
            "输入缓冲", "土狼时间", "加速度", "滑行",
            "挤压拉伸", "果汁感", "反馈",
            "game feel", "juice", "hitstop", "knockback",
            "screen shake", "input buffer", "coyote time",
            "squash stretch", "acceleration", "deceleration",
            "responsiveness", "weight", "impact",
        ],
        TargetModule.PIXEL_ART: [
            "像素", "像素画", "抖动", "子像素", "限色",
            "pixel art", "dithering", "sub-pixel", "limited palette",
            "RotSprite", "anti-alias", "cluster", "jaggies",
            "像素风格", "8bit", "16bit", "retro",
        ],
        TargetModule.PBR: [
            "PBR", "法线贴图", "BRDF", "Cook-Torrance", "菲涅尔",
            "金属度", "粗糙度", "环境光遮蔽", "AO",
            "physically based", "normal map", "metallic", "roughness",
            "fresnel", "specular", "diffuse", "albedo",
            "光照模型", "lighting model",
        ],
        TargetModule.PHYSICS: [
            "物理", "弹簧", "阻尼", "碰撞", "刚体", "重力",
            "弹性", "摩擦", "惯性", "加速度", "速度",
            "physics", "spring", "damping", "collision", "rigid body",
            "gravity", "elasticity", "friction", "inertia",
            "verlet", "euler", "runge-kutta",
        ],
        TargetModule.VFX: [
            "特效", "粒子", "爆炸", "火焰", "烟雾", "闪电",
            "拖尾", "光晕", "残影",
            "VFX", "particle", "explosion", "flame", "smoke",
            "lightning", "trail", "glow", "afterimage",
        ],
        TargetModule.SHADER: [
            "着色器", "shader", "GLSL", "HLSL", "GPU",
            "顶点", "片元", "渲染管线", "光线行进",
            "vertex", "fragment", "render pipeline", "ray marching",
            "compute shader", "post-processing",
        ],
        TargetModule.NARRATIVE: [
            "叙事", "故事", "剧情", "世界观", "角色设定",
            "节奏", "三幕式", "英雄之旅", "伏笔",
            "narrative", "story", "plot", "worldbuilding",
            "pacing", "three-act", "hero's journey", "foreshadowing",
            "对话", "dialogue", "lore",
        ],
        TargetModule.PERSPECTIVE: [
            "透视", "消失点", "地平线", "前缩", "深度",
            "一点透视", "两点透视", "三点透视", "鱼眼",
            "perspective", "vanishing point", "horizon", "foreshortening",
            "depth cue", "overlap", "projection",
            "OCHABI", "吉田誠治", "Scott Robertson",
        ],
        TargetModule.PROGRAMMING: [
            "程序", "状态机", "数据驱动", "程序化生成",
            "设计模式", "架构", "ECS", "FSM",
            "对象池", "事件系统", "性能优化",
            "programming", "state machine", "data-driven",
            "procedural generation", "PCG", "design pattern",
            "architecture", "object pool", "event system",
            "component", "entity", "system",
        ],
        TargetModule.GENERAL: [
            # GENERAL is the fallback; these keywords boost its score
            # only when no other module matches well
            "数学", "算法", "优化", "工具", "管线", "工作流",
            "math", "algorithm", "optimization", "pipeline", "workflow",
        ],
    }

    # ── Filename-to-module hint ─────────────────────────────────────
    # When parsing a knowledge file, the filename provides a strong hint
    _FILENAME_MODULE_HINT = {
        "anatomy": TargetModule.ANATOMY,
        "animation": TargetModule.ANIMATION,
        "color_light": TargetModule.OKLAB,
        "perspective": TargetModule.PERSPECTIVE,
        "unity_rules": TargetModule.EXPORT,
        "pixel_art": TargetModule.PIXEL_ART,
        "game_design": TargetModule.GAME_DESIGN,
        "plant_botany": TargetModule.LSYSTEM,
        "vfx": TargetModule.VFX,
        "pbr_lighting": TargetModule.PBR,
        "shader_math": TargetModule.SHADER,
        "physics_sim": TargetModule.PHYSICS,
        "narrative": TargetModule.NARRATIVE,
        "level_design": TargetModule.LEVEL_DESIGN,
        "game_feel": TargetModule.GAME_FEEL,
        "programming": TargetModule.PROGRAMMING,
    }

    def __init__(self):
        self._rule_counter = 0

    def parse_directory(self, directory: Union[str, Path]) -> list[KnowledgeRule]:
        """Parse all knowledge files in a directory."""
        directory = Path(directory)
        rules = []
        for md_file in sorted(directory.glob("*.md")):
            rules.extend(self.parse_markdown(md_file))
        for json_file in sorted(directory.glob("*.json")):
            rules.extend(self.load_rules(json_file))
        return rules

    def parse_markdown(self, filepath: Union[str, Path]) -> list[KnowledgeRule]:
        """Parse a Markdown knowledge file into rules."""
        filepath = Path(filepath)
        text = filepath.read_text(encoding="utf-8")
        filename_stem = filepath.stem  # e.g., "anatomy", "color_light"

        # Resolve filename hint for module detection bias
        self._current_file_hint = self._FILENAME_MODULE_HINT.get(filename_stem)

        rules = []
        current_source = ""

        # Extract source attribution
        source_match = self._SOURCE_RE.search(text)
        if source_match:
            current_source = source_match.group(1).strip()

        # Process by sections
        sections = self._SECTION_RE.split(text)
        for i in range(1, len(sections), 2):
            section_title = sections[i].strip()
            section_body = sections[i + 1] if i + 1 < len(sections) else ""

            # Extract rules from tables
            table_rules = self._extract_table_rules(
                section_body, filename_stem, section_title, current_source
            )
            rules.extend(table_rules)

            # Extract rules from bullet points and paragraphs
            text_rules = self._extract_text_rules(
                section_body, filename_stem, section_title, current_source
            )
            rules.extend(text_rules)

        self._current_file_hint = None
        return rules

    def _extract_table_rules(
        self, text: str, file_stem: str, section: str, source: str
    ) -> list[KnowledgeRule]:
        """Extract rules from Markdown tables."""
        rules = []
        lines = text.strip().split("\n")
        in_table = False
        headers = []

        for line in lines:
            line = line.strip()
            if not line.startswith("|"):
                in_table = False
                headers = []
                continue

            cells = [c.strip() for c in line.split("|")[1:-1]]
            if not cells:
                continue

            # Skip separator rows
            if all(set(c) <= {"-", ":", " "} for c in cells):
                in_table = True
                continue

            if not in_table and not headers:
                headers = [c.lower() for c in cells]
                continue

            if len(cells) >= 2:
                self._rule_counter += 1
                rule_id = f"{file_stem}_{self._rule_counter:03d}"

                param_name = cells[0]
                value_str = cells[1]
                description = cells[2] if len(cells) > 2 else f"{param_name}: {value_str}"

                # Detect target module
                target_module = self._detect_module(
                    f"{section} {param_name} {description}"
                )

                # Parse constraint from value
                constraint = self._parse_constraint(value_str)

                # Determine rule type
                rule_type = self._classify_rule_type(constraint, description)

                rules.append(
                    KnowledgeRule(
                        id=rule_id,
                        description=description.strip(),
                        rule_type=rule_type,
                        target_module=target_module,
                        target_param=param_name.strip(),
                        constraint=constraint,
                        source=source,
                        tags=[file_stem, section.lower()],
                    )
                )

        return rules

    def _extract_text_rules(
        self, text: str, file_stem: str, section: str, source: str
    ) -> list[KnowledgeRule]:
        """Extract rules from free-form text (bullet points, paragraphs)."""
        rules = []
        # Match bullet points with numeric values
        bullet_re = re.compile(
            r"[-*]\s+(.+?)(?:：|:)\s*(.+?)(?:\n|$)"
        )
        for match in bullet_re.finditer(text):
            param = match.group(1).strip()
            value = match.group(2).strip()

            # Only extract if there's a quantifiable value
            if re.search(r"\d", value):
                self._rule_counter += 1
                rule_id = f"{file_stem}_{self._rule_counter:03d}"
                target_module = self._detect_module(f"{section} {param} {value}")
                constraint = self._parse_constraint(value)

                if constraint:  # Only add if we could parse a constraint
                    rules.append(
                        KnowledgeRule(
                            id=rule_id,
                            description=f"{param}: {value}",
                            rule_type=self._classify_rule_type(constraint, value),
                            target_module=target_module,
                            target_param=param,
                            constraint=constraint,
                            source=source,
                            tags=[file_stem, section.lower()],
                        )
                    )

        return rules

    def _detect_module(self, text: str) -> TargetModule:
        """Detect which module a rule applies to based on keyword matching.

        Uses a scoring system with filename hints as tiebreakers.
        """
        scores: dict[TargetModule, int] = {}
        text_lower = text.lower()
        for module, keywords in self._MODULE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            scores[module] = score

        best = max(scores, key=scores.get)
        best_score = scores[best]

        # If no keywords matched, use filename hint or fallback to GENERAL
        if best_score == 0:
            hint = getattr(self, "_current_file_hint", None)
            if hint is not None:
                return hint
            return TargetModule.GENERAL

        # If there's a tie, prefer the filename hint if it's among the tied
        hint = getattr(self, "_current_file_hint", None)
        if hint is not None and scores.get(hint, 0) == best_score:
            return hint

        return best

    def _parse_constraint(self, value_str: str) -> dict:
        """Parse a value string into a structured constraint."""
        value_str = value_str.strip()

        # Range: "0-180", "0.1 ~ 0.9", "-45–45"
        range_match = self._RANGE_RE.search(value_str)
        if range_match:
            return {
                "type": "range",
                "min": float(range_match.group(1)),
                "max": float(range_match.group(2)),
            }

        # Single number
        num_match = re.match(r"^(-?\d+\.?\d*)\s*(\w*)$", value_str)
        if num_match:
            return {
                "type": "exact",
                "value": float(num_match.group(1)),
                "unit": num_match.group(2) or None,
            }

        # Enum-like: "A/B/C" or "A, B, C"
        if "/" in value_str or (", " in value_str and not any(c.isdigit() for c in value_str)):
            sep = "/" if "/" in value_str else ", "
            values = [v.strip() for v in value_str.split(sep)]
            if len(values) >= 2:
                return {"type": "enum", "values": values}

        # Boolean
        if value_str.lower() in ("true", "false", "yes", "no", "是", "否"):
            return {
                "type": "boolean",
                "value": value_str.lower() in ("true", "yes", "是"),
            }

        # Fallback: store as text
        return {"type": "text", "value": value_str}

    def _classify_rule_type(self, constraint: dict, description: str) -> RuleType:
        """Classify a rule as hard constraint, soft default, or heuristic."""
        desc_lower = description.lower()

        # Hard constraints: exact values, strict ranges, "must", "必须"
        hard_keywords = ["must", "必须", "强制", "ppu", "pivot", "filter"]
        if any(kw in desc_lower for kw in hard_keywords):
            return RuleType.HARD_CONSTRAINT
        if constraint.get("type") == "exact":
            return RuleType.HARD_CONSTRAINT

        # Soft defaults: ranges, recommended values
        if constraint.get("type") in ("range", "enum"):
            return RuleType.SOFT_DEFAULT

        return RuleType.HEURISTIC

    # ── Serialization ────────────────────────────────────────────────

    @staticmethod
    def save_rules(
        rules: list[KnowledgeRule], filepath: Union[str, Path]
    ) -> None:
        """Save rules to a JSON file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in rules]
        filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def load_rules(filepath: Union[str, Path]) -> list[KnowledgeRule]:
        """Load rules from a JSON file."""
        filepath = Path(filepath)
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return [KnowledgeRule.from_dict(d) for d in data]

    @staticmethod
    def rules_summary(rules: list[KnowledgeRule]) -> dict:
        """Generate a summary of parsed rules."""
        by_module = {}
        by_type = {}
        for r in rules:
            mod = r.target_module.value
            by_module[mod] = by_module.get(mod, 0) + 1
            rt = r.rule_type.value
            by_type[rt] = by_type.get(rt, 0) + 1
        return {
            "total_rules": len(rules),
            "by_module": by_module,
            "by_type": by_type,
        }

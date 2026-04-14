"""Perception Layer: Knowledge extraction and structuring.

This module parses knowledge sources (Markdown files, plain text, and PDF
documents) into structured ``KnowledgeRule`` objects. Each rule captures:

- A human-readable description of the artistic/design principle.
- The affected module(s) and parameter(s).
- Quantitative constraints (ranges, enums, formulas).
- Source attribution (book, author, page).

The parser understands the table-based format used in ``knowledge/*.md`` files
and can also extract rules from free-form text via pattern matching.
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
    """Modules that can be affected by distilled knowledge."""

    ANIMATION = "animation"
    OKLAB = "oklab"
    SDF = "sdf"
    EXPORT = "export"
    LEVEL = "level"


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

    Usage::

        parser = KnowledgeParser()
        rules = parser.parse_directory("mathart/knowledge/")
        parser.save_rules(rules, "rules.json")
    """

    # Regex patterns for extracting structured info from Markdown tables
    _TABLE_ROW_RE = re.compile(r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|")
    _RANGE_RE = re.compile(r"(-?\d+\.?\d*)\s*[-–~]\s*(-?\d+\.?\d*)")
    _SOURCE_RE = re.compile(r"来源[：:]\s*(.+?)(?:\n|$)", re.IGNORECASE)
    _SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

    # Module keyword mapping
    _MODULE_KEYWORDS = {
        TargetModule.ANIMATION: [
            "关节", "骨骼", "动画", "帧", "ROM", "角度", "运动",
            "joint", "skeleton", "animation", "frame", "motion",
            "弹簧", "阻尼", "spring", "damping",
        ],
        TargetModule.OKLAB: [
            "色彩", "调色", "色相", "饱和", "明度", "暖光", "冷影",
            "color", "palette", "hue", "saturation", "lightness",
        ],
        TargetModule.SDF: [
            "SDF", "距离场", "特效", "粒子", "形状", "轮廓",
            "effect", "shape", "outline", "glow",
        ],
        TargetModule.EXPORT: [
            "PPU", "Unity", "导出", "pivot", "filter",
            "export", "sprite",
        ],
        TargetModule.LEVEL: [
            "关卡", "地形", "布局", "WFC", "生成",
            "level", "terrain", "layout", "tile",
        ],
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

        rules = []
        current_source = ""
        current_section = ""

        # Extract source attribution
        source_match = self._SOURCE_RE.search(text)
        if source_match:
            current_source = source_match.group(1).strip()

        # Process by sections
        sections = self._SECTION_RE.split(text)
        for i in range(1, len(sections), 2):
            section_title = sections[i].strip()
            section_body = sections[i + 1] if i + 1 < len(sections) else ""
            current_section = section_title

            # Extract rules from tables
            table_rules = self._extract_table_rules(
                section_body, filename_stem, current_section, current_source
            )
            rules.extend(table_rules)

            # Extract rules from bullet points and paragraphs
            text_rules = self._extract_text_rules(
                section_body, filename_stem, current_section, current_source
            )
            rules.extend(text_rules)

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
        """Detect which module a rule applies to based on keyword matching."""
        scores = {}
        text_lower = text.lower()
        for module, keywords in self._MODULE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw.lower() in text_lower)
            scores[module] = score

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
        return TargetModule.ANIMATION  # Default fallback

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

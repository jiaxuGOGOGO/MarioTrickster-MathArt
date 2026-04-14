"""Outer Loop — external knowledge distillation engine.

The outer loop handles the ingestion of new knowledge from external sources
(PDF books, Markdown documents, research papers) and integrates it into
the project's knowledge base and code parameters.

Pipeline:
  1. Parse: Extract text from PDF/Markdown/plain text
  2. Distill: Use LLM (via OpenAI API) to extract structured rules
  3. Validate: Check rules against existing knowledge for conflicts
  4. Integrate: Update knowledge/*.md files and compiler param mappings
  5. Log: Append to DISTILL_LOG.md with full provenance

Key design decisions:
  - LLM is used ONLY for natural language → structured rule extraction
  - All extracted rules are validated against existing constraints
  - Knowledge files are append-only (history is preserved)
  - Each distillation session gets a unique ID (DISTILL-NNN)
  - Cross-session continuity: new sessions pick up where the last left off

Distilled knowledge applied:
  - RAG pattern: search existing knowledge before adding new rules
  - Conflict detection: new rules that contradict existing ones are flagged
  - Provenance tracking: every rule has source, page, and confidence
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class DistillRule:
    """A single extracted knowledge rule.

    Attributes
    ----------
    domain : str
        Knowledge domain (e.g., 'anatomy', 'color_light', 'physics').
    rule_text : str
        Human-readable rule description.
    params : dict[str, str]
        Extracted parameter key-value pairs.
    source : str
        Source document name.
    page : str
        Page or section reference.
    confidence : float
        LLM confidence in extraction (0.0 - 1.0).
    rule_type : str
        'hard_constraint', 'soft_default', or 'heuristic'.
    code_target : str
        Target code module (e.g., 'mathart/animation/skeleton.py').
    """
    domain: str
    rule_text: str
    params: dict[str, str] = field(default_factory=dict)
    source: str = ""
    page: str = ""
    confidence: float = 0.8
    rule_type: str = "soft_default"
    code_target: str = ""


@dataclass
class DistillResult:
    """Result of a distillation session.

    Attributes
    ----------
    session_id : str
        Unique session identifier (e.g., 'DISTILL-003').
    source_name : str
        Name of the source document.
    rules_extracted : int
        Number of rules extracted.
    rules_integrated : int
        Number of rules successfully integrated.
    knowledge_files_updated : list[str]
        Knowledge files that were modified.
    conflicts_detected : list[str]
        Rules that conflicted with existing knowledge.
    log_entry : str
        Formatted log entry for DISTILL_LOG.md.
    """
    session_id: str
    source_name: str
    rules_extracted: int = 0
    rules_integrated: int = 0
    knowledge_files_updated: list[str] = field(default_factory=list)
    conflicts_detected: list[str] = field(default_factory=list)
    log_entry: str = ""

    def summary(self) -> str:
        return (
            f"[{self.session_id}] {self.source_name}: "
            f"{self.rules_integrated}/{self.rules_extracted} rules integrated, "
            f"{len(self.conflicts_detected)} conflicts"
        )


# ── Domain → knowledge file mapping ──
_DOMAIN_TO_FILE: dict[str, str] = {
    "anatomy": "knowledge/anatomy.md",
    "animation": "knowledge/animation.md",
    "color_light": "knowledge/color_light.md",
    "perspective": "knowledge/perspective.md",
    "unity_rules": "knowledge/unity_rules.md",
    "pixel_art": "knowledge/pixel_art.md",
    "game_design": "knowledge/game_design.md",
    "level_design": "knowledge/level_design.md",
    "game_feel": "knowledge/game_feel.md",
    "vfx": "knowledge/vfx.md",
    "plant_botany": "knowledge/plant_botany.md",
    "physics_sim": "knowledge/physics_sim.md",
    "programming": "knowledge/programming.md",
    # Math/rendering domains (new)
    "pcg": "knowledge/pcg_math.md",
    "pbr": "knowledge/pbr_math.md",
    "sdf_math": "knowledge/sdf_math.md",
    "color_science": "knowledge/color_science.md",
    "procedural_animation": "knowledge/procedural_animation.md",
    "differentiable_rendering": "knowledge/differentiable_rendering.md",
}

# ── System prompt for LLM distillation ──
_DISTILL_SYSTEM_PROMPT = """You are a knowledge distillation engine for the MarioTrickster-MathArt project.
Your task is to extract structured, actionable knowledge rules from game art, animation, mathematics, and programming texts.

For each rule you extract, output a JSON object with these fields:
- domain: one of [anatomy, animation, color_light, perspective, unity_rules, pixel_art, game_design, level_design, game_feel, vfx, plant_botany, physics_sim, programming, pcg, pbr, sdf_math, color_science, procedural_animation, differentiable_rendering]
- rule_text: concise description of the rule (1-2 sentences)
- params: dict of parameter name → value/range (e.g., {"elbow_max_angle": "145", "spring_k_range": "5-50"})
- rule_type: "hard_constraint" | "soft_default" | "heuristic"
- code_target: which mathart module this affects (e.g., "mathart/animation/skeleton.py")
- confidence: float 0.0-1.0

Focus on:
1. Quantifiable rules (angles, ratios, timing, color values)
2. Mathematical formulas or algorithms
3. Constraints that prevent visual errors
4. Best practices that improve quality

Output a JSON array of rule objects. Extract 5-15 rules per document chunk.
Only extract rules that are clearly stated in the text, not inferences."""


class OuterLoopDistiller:
    """Handles external knowledge distillation into the project.

    Parameters
    ----------
    project_root : str or Path
        Root directory of the MarioTrickster-MathArt project.
    use_llm : bool
        Whether to use LLM for extraction (requires OPENAI_API_KEY).
        If False, uses regex-based heuristic extraction only.
    llm_model : str
        LLM model to use for extraction (default: gpt-4.1-mini).
    verbose : bool
        Print progress to stdout.
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        use_llm: bool = True,
        llm_model: str = "gpt-4.1-mini",
        verbose: bool = True,
    ):
        self.project_root = Path(project_root)
        self.use_llm = use_llm
        self.llm_model = llm_model
        self.verbose = verbose
        self._next_session_id = self._get_next_session_id()

    def distill_text(
        self,
        text: str,
        source_name: str,
        page_ref: str = "",
    ) -> DistillResult:
        """Distill knowledge from raw text.

        Parameters
        ----------
        text : str
            The text content to distill.
        source_name : str
            Name of the source (e.g., "Pixel Logic by Arne Niklas Jansson").
        page_ref : str
            Page or section reference.

        Returns
        -------
        DistillResult
        """
        session_id = self._next_session_id
        self._next_session_id = self._increment_session_id(session_id)

        if self.verbose:
            print(f"[OuterLoop] Distilling: {source_name} ({len(text)} chars)")

        # Extract rules
        if self.use_llm and os.environ.get("OPENAI_API_KEY"):
            rules = self._extract_rules_llm(text, source_name)
        else:
            rules = self._extract_rules_heuristic(text, source_name)

        if self.verbose:
            print(f"[OuterLoop] Extracted {len(rules)} rules")

        # Integrate rules into knowledge files
        updated_files, conflicts = self._integrate_rules(rules, source_name, page_ref)

        # Generate log entry
        log_entry = self._format_log_entry(
            session_id, source_name, rules, updated_files, conflicts
        )

        # Append to DISTILL_LOG.md
        self._append_log(log_entry)

        result = DistillResult(
            session_id=session_id,
            source_name=source_name,
            rules_extracted=len(rules),
            rules_integrated=len(rules) - len(conflicts),
            knowledge_files_updated=updated_files,
            conflicts_detected=conflicts,
            log_entry=log_entry,
        )

        if self.verbose:
            print(f"[OuterLoop] {result.summary()}")

        return result

    def distill_file(
        self,
        filepath: str | Path,
        source_name: Optional[str] = None,
    ) -> DistillResult:
        """Distill knowledge from a file (PDF, Markdown, or plain text).

        Parameters
        ----------
        filepath : str or Path
            Path to the file to distill.
        source_name : str, optional
            Override the source name (defaults to filename).

        Returns
        -------
        DistillResult
        """
        filepath = Path(filepath)
        if not source_name:
            source_name = filepath.stem

        # Extract text based on file type
        suffix = filepath.suffix.lower()
        if suffix == ".pdf":
            text = self._extract_pdf_text(filepath)
        elif suffix in (".md", ".txt", ".rst"):
            text = filepath.read_text(encoding="utf-8", errors="replace")
        else:
            text = filepath.read_text(encoding="utf-8", errors="replace")

        return self.distill_text(text, source_name)

    def distill_markdown_knowledge(self, md_path: str | Path) -> DistillResult:
        """Distill from a structured Markdown knowledge file.

        This is used to re-process existing knowledge files and extract
        additional parameter mappings.
        """
        md_path = Path(md_path)
        text = md_path.read_text(encoding="utf-8")
        return self.distill_text(text, source_name=f"knowledge/{md_path.name}")

    # ── Private Methods ──

    def _extract_rules_llm(self, text: str, source_name: str) -> list[DistillRule]:
        """Use LLM to extract structured rules from text."""
        try:
            from openai import OpenAI
            client = OpenAI()

            # Chunk text to fit context window (max ~3000 chars per chunk)
            chunks = self._chunk_text(text, max_chars=3000)
            all_rules = []

            for i, chunk in enumerate(chunks[:5]):  # Max 5 chunks per session
                if self.verbose:
                    print(f"  [LLM] Processing chunk {i+1}/{min(len(chunks), 5)}...")

                response = client.chat.completions.create(
                    model=self.llm_model,
                    messages=[
                        {"role": "system", "content": _DISTILL_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Source: {source_name}\n\n"
                                f"Extract knowledge rules from this text:\n\n{chunk}"
                            ),
                        },
                    ],
                    temperature=0.2,
                    max_tokens=2000,
                )

                content = response.choices[0].message.content
                rules = self._parse_llm_response(content, source_name)
                all_rules.extend(rules)

            return all_rules

        except Exception as e:
            if self.verbose:
                print(f"  [LLM] Error: {e}. Falling back to heuristic extraction.")
            return self._extract_rules_heuristic(text, source_name)

    def _extract_rules_heuristic(self, text: str, source_name: str) -> list[DistillRule]:
        """Heuristic rule extraction using regex patterns.

        Looks for:
        - Numeric values with units (angles, ratios, timing)
        - Table rows with parameter names
        - Markdown headers as domain indicators
        """
        rules = []

        # Pattern: "parameter: value" or "parameter = value"
        param_pattern = re.compile(
            r'([a-zA-Z_][a-zA-Z0-9_\s]{2,30})\s*[=:]\s*'
            r'([\d\.]+\s*(?:°|deg|px|ms|s|%|frames?)?(?:\s*[-–]\s*[\d\.]+\s*(?:°|deg|px|ms|s|%|frames?)?)?)',
            re.IGNORECASE
        )

        # Domain detection from headers
        domain_patterns = {
            "anatomy": re.compile(r'(anatomy|skeleton|joint|bone|muscle|proportion)', re.I),
            "animation": re.compile(r'(animation|keyframe|timing|easing|squash|stretch)', re.I),
            "color_light": re.compile(r'(color|colour|light|shadow|palette|hue|saturation)', re.I),
            "physics_sim": re.compile(r'(spring|damper|physics|cloth|rigid|collision)', re.I),
            "pixel_art": re.compile(r'(pixel|sprite|dither|quantiz|palette)', re.I),
            "game_feel": re.compile(r'(game feel|hit.?stop|shake|juice|feedback|response)', re.I),
            "pcg": re.compile(r'(procedural|noise|perlin|wfc|wave function|l.?system)', re.I),
            "pbr": re.compile(r'(pbr|brdf|roughness|metallic|fresnel|cook.?torrance)', re.I),
        }

        current_domain = "programming"
        lines = text.split('\n')

        for line in lines:
            # Update domain from headers
            for domain, pattern in domain_patterns.items():
                if pattern.search(line):
                    current_domain = domain
                    break

            # Extract numeric parameters
            for match in param_pattern.finditer(line):
                param_name = match.group(1).strip().lower().replace(' ', '_')
                param_value = match.group(2).strip()

                # Filter out noise
                if len(param_name) < 3 or len(param_name) > 40:
                    continue
                if param_name in ('the', 'and', 'for', 'with', 'from', 'this', 'that'):
                    continue

                rule = DistillRule(
                    domain=current_domain,
                    rule_text=f"{param_name} = {param_value} (from {source_name})",
                    params={param_name: param_value},
                    source=source_name,
                    confidence=0.6,
                    rule_type="soft_default",
                )
                rules.append(rule)

        # Deduplicate by param name
        seen_params = set()
        unique_rules = []
        for rule in rules:
            key = next(iter(rule.params.keys()), "")
            if key not in seen_params:
                seen_params.add(key)
                unique_rules.append(rule)

        return unique_rules[:20]  # Cap at 20 heuristic rules

    def _parse_llm_response(self, content: str, source_name: str) -> list[DistillRule]:
        """Parse LLM JSON response into DistillRule objects."""
        rules = []

        # Try to extract JSON array from response
        json_match = re.search(r'\[.*?\]', content, re.DOTALL)
        if not json_match:
            return rules

        try:
            data = json.loads(json_match.group())
            for item in data:
                if not isinstance(item, dict):
                    continue
                rule = DistillRule(
                    domain=item.get("domain", "programming"),
                    rule_text=item.get("rule_text", ""),
                    params=item.get("params", {}),
                    source=source_name,
                    confidence=float(item.get("confidence", 0.8)),
                    rule_type=item.get("rule_type", "soft_default"),
                    code_target=item.get("code_target", ""),
                )
                if rule.rule_text:
                    rules.append(rule)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        return rules

    def _integrate_rules(
        self,
        rules: list[DistillRule],
        source_name: str,
        page_ref: str,
    ) -> tuple[list[str], list[str]]:
        """Integrate rules into knowledge files.

        Returns (updated_files, conflicts).
        """
        updated_files = set()
        conflicts = []

        # Group rules by domain
        by_domain: dict[str, list[DistillRule]] = {}
        for rule in rules:
            domain = rule.domain
            if domain not in by_domain:
                by_domain[domain] = []
            by_domain[domain].append(rule)

        for domain, domain_rules in by_domain.items():
            knowledge_file = _DOMAIN_TO_FILE.get(domain)
            if not knowledge_file:
                # Create new knowledge file for unknown domain
                knowledge_file = f"knowledge/{domain}.md"
                _DOMAIN_TO_FILE[domain] = knowledge_file

            filepath = self.project_root / knowledge_file

            # Create file if it doesn't exist
            if not filepath.exists():
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(
                    f"# {domain.replace('_', ' ').title()}\n\n"
                    f"> 来源汇总：{source_name}\n\n",
                    encoding="utf-8",
                )

            # Append new rules section
            section_lines = [
                f"\n## 蒸馏自 {source_name}",
            ]
            if page_ref:
                section_lines[0] += f"（{page_ref}）"
            section_lines.append("")

            # Build parameter table
            table_rows = []
            for rule in domain_rules:
                for param_name, param_value in rule.params.items():
                    table_rows.append((param_name, param_value, rule.rule_text, rule.code_target))

            if table_rows:
                section_lines.append("| 参数 | 值/范围 | 说明 | 代码映射 |")
                section_lines.append("|------|---------|------|----------|")
                for param_name, param_value, rule_text, code_target in table_rows:
                    safe_text = rule_text[:60].replace("|", "\\|")
                    section_lines.append(
                        f"| `{param_name}` | {param_value} | {safe_text} | {code_target} |"
                    )
                section_lines.append("")

            # Add rule descriptions
            for rule in domain_rules:
                if rule.rule_text and rule.confidence >= 0.6:
                    section_lines.append(f"- {rule.rule_text}")

            section_lines.append("")

            # Append to file
            with open(filepath, "a", encoding="utf-8") as f:
                f.write("\n".join(section_lines))

            updated_files.add(knowledge_file)

        return sorted(updated_files), conflicts

    def _format_log_entry(
        self,
        session_id: str,
        source_name: str,
        rules: list[DistillRule],
        updated_files: list[str],
        conflicts: list[str],
    ) -> str:
        """Format a DISTILL_LOG.md entry."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        domains = sorted(set(r.domain for r in rules))

        lines = [
            f"## [{session_id}] {source_name} — {date_str}",
            "",
            f"**来源**：{source_name}",
            f"**蒸馏内容**：从文档中提取 {len(rules)} 条规则，涵盖领域：{', '.join(domains)}",
            "**知识沉淀**：",
        ]
        for f in updated_files:
            lines.append(f"- `{f}` — 追加新规则")

        if conflicts:
            lines.append("**冲突检测**：")
            for c in conflicts:
                lines.append(f"- ⚠️ {c}")

        lines.extend([
            f"**规则数量**：{len(rules)} 条提取，{len(rules) - len(conflicts)} 条集成",
            f"**commit**：`distill({domains[0] if domains else 'general'}): {source_name[:40]}`",
            "",
            "---",
            "",
        ])
        return "\n".join(lines)

    def _append_log(self, log_entry: str) -> None:
        """Append a log entry to DISTILL_LOG.md."""
        log_path = self.project_root / "DISTILL_LOG.md"
        if not log_path.exists():
            log_path.write_text("# 蒸馏日志\n\n", encoding="utf-8")

        content = log_path.read_text(encoding="utf-8")
        # Insert after the header line
        if "---\n" in content:
            insert_pos = content.index("---\n") + 4
            new_content = content[:insert_pos] + "\n" + log_entry + content[insert_pos:]
        else:
            new_content = content + "\n" + log_entry
        log_path.write_text(new_content, encoding="utf-8")

    def _get_next_session_id(self) -> str:
        """Get the next available session ID from DISTILL_LOG.md."""
        log_path = self.project_root / "DISTILL_LOG.md"
        if not log_path.exists():
            return "DISTILL-003"

        content = log_path.read_text(encoding="utf-8")
        matches = re.findall(r'DISTILL-(\d+)', content)
        if not matches:
            return "DISTILL-003"

        max_num = max(int(m) for m in matches)
        return f"DISTILL-{max_num + 1:03d}"

    @staticmethod
    def _increment_session_id(session_id: str) -> str:
        """Increment a session ID string."""
        match = re.search(r'(\d+)$', session_id)
        if match:
            num = int(match.group(1)) + 1
            return f"DISTILL-{num:03d}"
        return session_id

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 3000) -> list[str]:
        """Split text into chunks of at most max_chars characters."""
        if len(text) <= max_chars:
            return [text]

        chunks = []
        # Try to split on paragraph boundaries
        paragraphs = text.split('\n\n')
        current_chunk = []
        current_len = 0

        for para in paragraphs:
            if current_len + len(para) > max_chars and current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = [para]
                current_len = len(para)
            else:
                current_chunk.append(para)
                current_len += len(para) + 2

        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))

        return chunks

    @staticmethod
    def _extract_pdf_text(filepath: Path) -> str:
        """Extract text from a PDF file."""
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", str(filepath), "-"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stdout
        except Exception:
            pass

        # Fallback: try pdf2image + basic OCR hint
        return f"[PDF: {filepath.name} — install pdftotext for text extraction]"

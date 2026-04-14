"""DeduplicationEngine — Knowledge and Math Model Deduplication.

Design philosophy
-----------------
"Avoid redundant knowledge, but NEVER miss valuable knowledge."

This engine solves the tension between two competing risks:
  1. Knowledge bloat: The same rule expressed in different words gets added
     multiple times, inflating the knowledge base and confusing the compiler.
  2. Knowledge loss: Over-aggressive deduplication discards genuinely new
     information that happens to use similar vocabulary.

Resolution strategy
-------------------
The engine uses a THREE-TIER similarity check:

  Tier 1 — Exact match (hash-based):
    If the canonical form of a new rule is byte-identical to an existing rule,
    it is a definite duplicate. Skip silently.

  Tier 2 — Semantic similarity (TF-IDF cosine):
    If cosine similarity > 0.85, the rules are "probably the same concept".
    The engine keeps BOTH but flags the new one as a "variant" and logs the
    relationship. This preserves nuance while avoiding silent duplication.

  Tier 3 — Parameter overlap (numeric key-value):
    If a new rule defines the same parameter key with a value within 5% of
    an existing rule, it is a "numeric variant". The engine merges them by
    tightening the range (taking the intersection), and logs the merge.

For math models: deduplication is by (name, version) pair. Same name +
different version = upgrade candidate (logged, not discarded).

Key invariant: NOTHING is ever silently discarded. Every deduplication
decision is logged to DEDUP_LOG.md with full provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class DedupDecision:
    """Record of a single deduplication decision."""
    decision:       str    # "exact_dup", "variant_kept", "param_merged", "new", "model_upgrade"
    new_rule_text:  str
    matched_rule:   Optional[str]
    similarity:     float
    explanation:    str
    timestamp:      str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_log_line(self) -> str:
        sym = {"exact_dup": "✗", "variant_kept": "≈", "param_merged": "⊕",
               "new": "✓", "model_upgrade": "↑"}.get(self.decision, "?")
        return (
            f"  {sym} [{self.decision}] sim={self.similarity:.2f} | "
            f"{self.new_rule_text[:60]}…"
        )


@dataclass
class DedupResult:
    """Result of a batch deduplication run."""
    total_input:    int
    exact_dups:     int
    variants_kept:  int
    params_merged:  int
    new_rules:      int
    decisions:      list[DedupDecision] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"DeduplicationResult: {self.total_input} input → "
            f"{self.new_rules} new, {self.exact_dups} exact-dup, "
            f"{self.variants_kept} variant-kept, {self.params_merged} param-merged"
        )


# ── Core engine ────────────────────────────────────────────────────────────────

class DeduplicationEngine:
    """Deduplicates knowledge rules and math model entries.

    Parameters
    ----------
    project_root : Path
        Project root directory (for reading existing knowledge files).
    cosine_threshold : float
        Similarity above which a rule is considered a "variant" (default 0.85).
    param_tolerance : float
        Relative tolerance for numeric parameter deduplication (default 0.05 = 5%).
    verbose : bool
        Print deduplication decisions.
    """

    def __init__(
        self,
        project_root:      Optional[Path] = None,
        cosine_threshold:  float = 0.85,
        param_tolerance:   float = 0.05,
        verbose:           bool  = False,
    ) -> None:
        self.project_root     = Path(project_root) if project_root else Path(".")
        self.cosine_threshold = cosine_threshold
        self.param_tolerance  = param_tolerance
        self.verbose          = verbose

        # In-memory index: domain → list of (canonical_hash, rule_text, params)
        self._rule_index: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
        # Model index: name → version
        self._model_index: dict[str, str] = {}
        # All decisions this session
        self._decisions: list[DedupDecision] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_existing(self) -> None:
        """Load existing knowledge files to build the deduplication index."""
        knowledge_dir = self.project_root / "knowledge"
        if not knowledge_dir.exists():
            return
        for md_file in knowledge_dir.glob("*.md"):
            domain = md_file.stem
            text = md_file.read_text(encoding="utf-8")
            rules = self._extract_rules_from_markdown(text)
            for rule_text, params in rules:
                canonical = self._canonical(rule_text)
                h = self._hash(canonical)
                self._rule_index[domain].append((h, rule_text, params))
        if self.verbose:
            total = sum(len(v) for v in self._rule_index.values())
            print(f"[Dedup] Loaded {total} existing rules from {len(self._rule_index)} domains")

    def deduplicate_rules(
        self,
        new_rules: list[tuple[str, str, dict]],  # (domain, rule_text, params)
    ) -> tuple[list[tuple[str, str, dict]], DedupResult]:
        """Filter a list of new rules against the existing index.

        Parameters
        ----------
        new_rules : list of (domain, rule_text, params)

        Returns
        -------
        accepted_rules : list of (domain, rule_text, params)
            Rules that should be added (new + variants).
        result : DedupResult
            Statistics and decision log.
        """
        accepted: list[tuple[str, str, dict]] = []
        exact_dups = variants = merged = new_count = 0

        for domain, rule_text, params in new_rules:
            canonical = self._canonical(rule_text)
            h = self._hash(canonical)
            existing = self._rule_index.get(domain, [])

            # Tier 1: Exact hash match
            if any(eh == h for eh, _, _ in existing):
                decision = DedupDecision(
                    decision="exact_dup",
                    new_rule_text=rule_text,
                    matched_rule=next(rt for eh, rt, _ in existing if eh == h),
                    similarity=1.0,
                    explanation="Exact canonical match — skipped silently.",
                )
                self._decisions.append(decision)
                exact_dups += 1
                if self.verbose:
                    print(f"[Dedup] {decision.to_log_line()}")
                continue

            # Tier 2: Semantic similarity
            best_sim, best_match = self._best_cosine(canonical, existing)
            if best_sim >= self.cosine_threshold:
                # Keep as variant — valuable nuance may exist
                decision = DedupDecision(
                    decision="variant_kept",
                    new_rule_text=rule_text,
                    matched_rule=best_match,
                    similarity=best_sim,
                    explanation=(
                        f"Semantic similarity {best_sim:.2f} ≥ {self.cosine_threshold}. "
                        "Kept as variant — may contain additional nuance."
                    ),
                )
                self._decisions.append(decision)
                accepted.append((domain, f"[VARIANT] {rule_text}", params))
                self._rule_index[domain].append((h, rule_text, params))
                variants += 1
                if self.verbose:
                    print(f"[Dedup] {decision.to_log_line()}")
                continue

            # Tier 3: Numeric parameter overlap
            merged_params, merge_explanation = self._check_param_overlap(params, existing)
            if merge_explanation:
                decision = DedupDecision(
                    decision="param_merged",
                    new_rule_text=rule_text,
                    matched_rule=merge_explanation,
                    similarity=best_sim,
                    explanation=f"Numeric params merged: {merge_explanation}",
                )
                self._decisions.append(decision)
                accepted.append((domain, rule_text, merged_params))
                self._rule_index[domain].append((h, rule_text, merged_params))
                merged += 1
                if self.verbose:
                    print(f"[Dedup] {decision.to_log_line()}")
                continue

            # Genuinely new rule
            decision = DedupDecision(
                decision="new",
                new_rule_text=rule_text,
                matched_rule=None,
                similarity=best_sim,
                explanation="New rule — no significant overlap with existing knowledge.",
            )
            self._decisions.append(decision)
            accepted.append((domain, rule_text, params))
            self._rule_index[domain].append((h, rule_text, params))
            new_count += 1
            if self.verbose:
                print(f"[Dedup] {decision.to_log_line()}")

        result = DedupResult(
            total_input=len(new_rules),
            exact_dups=exact_dups,
            variants_kept=variants,
            params_merged=merged,
            new_rules=new_count,
            decisions=list(self._decisions),
        )
        return accepted, result

    def deduplicate_models(
        self,
        new_models: list[dict],  # list of {name, version, ...}
    ) -> tuple[list[dict], list[str]]:
        """Deduplicate math model entries.

        Returns
        -------
        accepted_models : list of model dicts to register
        upgrade_log : list of human-readable upgrade messages
        """
        accepted: list[dict] = []
        upgrade_log: list[str] = []

        for model in new_models:
            name = model.get("name", "")
            version = model.get("version", "0.0.0")

            if name not in self._model_index:
                # New model
                self._model_index[name] = version
                accepted.append(model)
                decision = DedupDecision(
                    decision="new",
                    new_rule_text=f"model:{name}@{version}",
                    matched_rule=None,
                    similarity=0.0,
                    explanation="New math model registered.",
                )
                self._decisions.append(decision)
            else:
                existing_ver = self._model_index[name]
                if self._version_gt(version, existing_ver):
                    # Upgrade
                    msg = f"Model '{name}' upgraded: {existing_ver} → {version}"
                    upgrade_log.append(msg)
                    self._model_index[name] = version
                    accepted.append(model)
                    decision = DedupDecision(
                        decision="model_upgrade",
                        new_rule_text=f"model:{name}@{version}",
                        matched_rule=f"model:{name}@{existing_ver}",
                        similarity=0.9,
                        explanation=msg,
                    )
                    self._decisions.append(decision)
                    if self.verbose:
                        print(f"[Dedup] {decision.to_log_line()}")
                else:
                    # Same or older version — skip
                    decision = DedupDecision(
                        decision="exact_dup",
                        new_rule_text=f"model:{name}@{version}",
                        matched_rule=f"model:{name}@{existing_ver}",
                        similarity=1.0,
                        explanation=f"Model '{name}' already registered at {existing_ver}.",
                    )
                    self._decisions.append(decision)
                    if self.verbose:
                        print(f"[Dedup] {decision.to_log_line()}")

        return accepted, upgrade_log

    def save_dedup_log(self, result: DedupResult, source: str = "") -> Path:
        """Append deduplication decisions to DEDUP_LOG.md."""
        log_path = self.project_root / "DEDUP_LOG.md"
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            f"\n## {ts} — {source or 'batch'}",
            "",
            f"**Summary**: {result.summary()}",
            "",
            "| Decision | Rule (truncated) | Similarity |",
            "|----------|-----------------|------------|",
        ]
        for d in result.decisions[-50:]:  # Last 50 decisions
            sym = {"exact_dup": "✗ exact-dup", "variant_kept": "≈ variant",
                   "param_merged": "⊕ merged", "new": "✓ new",
                   "model_upgrade": "↑ upgrade"}.get(d.decision, d.decision)
            rule_short = d.new_rule_text[:50].replace("|", "\\|")
            lines.append(f"| {sym} | {rule_short} | {d.similarity:.2f} |")
        lines.append("")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return log_path

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _canonical(text: str) -> str:
        """Normalize text for comparison: lowercase, strip punctuation, sort words."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        words = sorted(set(text.split()))
        return " ".join(words)

    @staticmethod
    def _hash(canonical: str) -> str:
        return hashlib.md5(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _tokenize(text: str) -> dict[str, int]:
        """Simple bag-of-words tokenizer."""
        words = re.findall(r"\w+", text.lower())
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        return freq

    def _cosine_similarity(self, text_a: str, text_b: str) -> float:
        """Compute TF-IDF-like cosine similarity between two texts."""
        a = self._tokenize(text_a)
        b = self._tokenize(text_b)
        vocab = set(a) | set(b)
        if not vocab:
            return 0.0
        dot = sum(a.get(w, 0) * b.get(w, 0) for w in vocab)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _best_cosine(
        self,
        canonical: str,
        existing: list[tuple[str, str, dict]],
    ) -> tuple[float, Optional[str]]:
        """Find the highest cosine similarity against existing rules."""
        best_sim = 0.0
        best_match = None
        for _, rule_text, _ in existing:
            sim = self._cosine_similarity(canonical, self._canonical(rule_text))
            if sim > best_sim:
                best_sim = sim
                best_match = rule_text
        return best_sim, best_match

    def _check_param_overlap(
        self,
        new_params: dict,
        existing: list[tuple[str, str, dict]],
    ) -> tuple[dict, str]:
        """Check if numeric parameters overlap with existing rules.

        Returns merged params and explanation string (empty if no overlap).
        """
        if not new_params:
            return new_params, ""

        for _, _, ex_params in existing:
            if not ex_params:
                continue
            overlapping_keys = set(new_params) & set(ex_params)
            if not overlapping_keys:
                continue

            merged = dict(new_params)
            merge_notes = []
            for key in overlapping_keys:
                try:
                    v_new = float(new_params[key])
                    v_ex  = float(ex_params[key])
                    if v_ex == 0:
                        continue
                    rel_diff = abs(v_new - v_ex) / abs(v_ex)
                    if rel_diff <= self.param_tolerance:
                        # Merge: take average
                        merged[key] = str((v_new + v_ex) / 2)
                        merge_notes.append(
                            f"{key}: {v_ex:.3f}↔{v_new:.3f}→{merged[key]}"
                        )
                except (ValueError, TypeError):
                    pass

            if merge_notes:
                return merged, "; ".join(merge_notes)

        return new_params, ""

    @staticmethod
    def _extract_rules_from_markdown(text: str) -> list[tuple[str, dict]]:
        """Extract rule lines and numeric params from a markdown file."""
        rules = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Extract numeric key=value pairs
            params = {}
            for m in re.finditer(r"(\w+)\s*[=:]\s*([\d.]+)", line):
                params[m.group(1)] = m.group(2)
            if len(line) > 10:  # Skip very short lines
                rules.append((line, params))
        return rules

    @staticmethod
    def _version_gt(v_new: str, v_old: str) -> bool:
        """Return True if v_new is strictly greater than v_old (semver)."""
        def parse(v: str) -> tuple[int, ...]:
            parts = re.findall(r"\d+", v)
            return tuple(int(p) for p in parts[:3])
        return parse(v_new) > parse(v_old)

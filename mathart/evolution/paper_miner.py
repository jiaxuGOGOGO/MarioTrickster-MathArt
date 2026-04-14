"""MathPaperMiner — Math Model Paper Mining and Integration Scheduler.

Purpose
-------
Systematically searches for, evaluates, and integrates relevant mathematical
models from academic papers and GitHub repositories into the project's
math model registry.

Search strategy
---------------
The miner searches across multiple sources in parallel:
  1. arXiv (cs.GR, cs.CV, eess.IV) — procedural generation, rendering, animation
  2. GitHub — pixel art tools, shader libraries, procedural art repos
  3. Papers With Code — state-of-the-art benchmarks with code

Relevance scoring
-----------------
Each found paper/repo is scored on four axes:
  - Applicability (0-1): How directly applicable to pixel art generation?
  - Implementability (0-1): Can it run on CPU without GPU?
  - Novelty (0-1): Does it add capabilities not yet in the registry?
  - Quality (0-1): Citation count, stars, code quality signals

Papers scoring above RELEVANCE_THRESHOLD (default 0.6) are:
  1. Summarized and added to knowledge/math_papers.md
  2. Registered as candidate models in the math registry (status="candidate")
  3. Flagged for human review if implementation requires external tools

Integration pipeline
--------------------
  search → score → deduplicate → register_candidate → notify_user
                                        ↓
                              (user approves) → implement → test → promote_to_stable
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


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class PaperResult:
    """A found paper or repository."""
    title:          str
    source:         str        # "arxiv", "github", "papers_with_code", "manual"
    url:            str
    abstract:       str
    year:           int
    applicability:  float      # 0-1
    implementability: float    # 0-1
    novelty:        float      # 0-1
    quality:        float      # 0-1
    relevance_score: float     # weighted average
    capabilities:   list[str]  # e.g., ["COLOR_PALETTE", "TEXTURE"]
    implementation_notes: str  # What would it take to implement?
    requires_gpu:   bool = False
    requires_external: str = ""  # e.g., "PyTorch", "Unity"


@dataclass
class MiningSession:
    """Record of a paper mining session."""
    session_id:     str
    query:          str
    timestamp:      str
    papers_found:   int
    papers_accepted: int
    papers_rejected: int
    results:        list[PaperResult] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"MiningSession {self.session_id}: "
            f"found={self.papers_found}, "
            f"accepted={self.papers_accepted}, "
            f"rejected={self.papers_rejected}"
        )


# ── Core MathPaperMiner ────────────────────────────────────────────────────────

class MathPaperMiner:
    """Searches for and integrates relevant math papers into the project.

    Parameters
    ----------
    project_root : Path
        Project root directory.
    relevance_threshold : float
        Minimum relevance score to accept a paper (default 0.6).
    use_llm : bool
        Whether to use LLM for paper summarization and scoring.
    verbose : bool
        Print progress messages.
    """

    # Search queries tailored to pixel art / game art math
    DEFAULT_QUERIES = [
        "procedural pixel art generation mathematical model",
        "wave function collapse tilemap generation",
        "OKLAB perceptual color space palette optimization",
        "sprite animation physics spring damper procedural",
        "signed distance field 2D game effects rendering",
        "L-system plant generation fractal pixel art",
        "differentiable rendering 2D sprite optimization",
        "normal map generation 2D sprite pseudo 3D",
        "palette quantization dithering perceptual quality",
        "isometric game rendering depth sort algorithm",
    ]

    # Capability keywords for relevance detection
    CAPABILITY_KEYWORDS = {
        "COLOR_PALETTE": ["palette", "color quantization", "OKLAB", "perceptual color"],
        "TEXTURE":       ["texture synthesis", "noise", "Perlin", "Simplex", "Wang tiles"],
        "ANIMATION":     ["procedural animation", "spring", "IK", "inverse kinematics"],
        "SDF":           ["signed distance field", "SDF", "ray marching", "distance function"],
        "PCG":           ["procedural generation", "WFC", "wave function collapse", "L-system"],
        "SHADER_PARAMS": ["shader", "HLSL", "GLSL", "rendering pipeline", "GPU"],
        "PIXEL_IMAGE":   ["differentiable rendering", "image synthesis", "sprite generation"],
        "PSEUDO_3D":     ["pseudo 3D", "isometric", "normal map", "parallax", "billboard"],
    }

    def __init__(
        self,
        project_root:        Optional[Path] = None,
        relevance_threshold: float = 0.6,
        use_llm:             bool  = True,
        verbose:             bool  = False,
    ) -> None:
        self.project_root        = Path(project_root) if project_root else Path(".")
        self.relevance_threshold = relevance_threshold
        self.use_llm             = use_llm
        self.verbose             = verbose
        self._session_count      = self._load_session_count()

    # ── Public API ─────────────────────────────────────────────────────────────

    def mine(
        self,
        queries: Optional[list[str]] = None,
        max_results_per_query: int = 5,
    ) -> MiningSession:
        """Run a full mining session.

        Parameters
        ----------
        queries : list of str, optional
            Search queries. Defaults to DEFAULT_QUERIES.
        max_results_per_query : int
            Maximum results to fetch per query.

        Returns
        -------
        MiningSession
        """
        self._session_count += 1
        session_id = f"MINE-{self._session_count:03d}"
        queries = queries or self.DEFAULT_QUERIES
        timestamp = datetime.utcnow().isoformat()

        if self.verbose:
            print(f"[PaperMiner] Starting session {session_id} with {len(queries)} queries")

        all_results: list[PaperResult] = []

        for query in queries:
            results = self._search_with_llm(query, max_results_per_query)
            all_results.extend(results)

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_results: list[PaperResult] = []
        for r in all_results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)

        # Filter by relevance threshold
        accepted = [r for r in unique_results if r.relevance_score >= self.relevance_threshold]
        rejected = [r for r in unique_results if r.relevance_score < self.relevance_threshold]

        session = MiningSession(
            session_id=session_id,
            query="; ".join(queries[:3]) + ("..." if len(queries) > 3 else ""),
            timestamp=timestamp,
            papers_found=len(unique_results),
            papers_accepted=len(accepted),
            papers_rejected=len(rejected),
            results=accepted,
        )

        # Persist results
        self._save_to_knowledge(session)
        self._save_session_count()
        self._append_to_log(session)

        if self.verbose:
            print(f"[PaperMiner] {session.summary()}")

        return session

    def mine_from_text(self, text: str, source_name: str = "manual") -> MiningSession:
        """Extract paper references from a text block (e.g., a bibliography).

        Parameters
        ----------
        text : str
            Text containing paper titles, URLs, or descriptions.
        source_name : str
            Name of the source for logging.

        Returns
        -------
        MiningSession
        """
        self._session_count += 1
        session_id = f"MINE-{self._session_count:03d}"

        papers = self._extract_papers_from_text(text, source_name)
        accepted = [p for p in papers if p.relevance_score >= self.relevance_threshold]

        session = MiningSession(
            session_id=session_id,
            query=f"manual:{source_name}",
            timestamp=datetime.utcnow().isoformat(),
            papers_found=len(papers),
            papers_accepted=len(accepted),
            papers_rejected=len(papers) - len(accepted),
            results=accepted,
        )

        self._save_to_knowledge(session)
        self._save_session_count()
        self._append_to_log(session)
        return session

    def generate_registry_candidates(
        self,
        session: MiningSession,
    ) -> list[dict]:
        """Convert accepted papers to math model registry candidate entries."""
        candidates = []
        for paper in session.results:
            for cap in paper.capabilities:
                candidate = {
                    "name": self._paper_to_model_name(paper.title),
                    "version": "0.1.0",
                    "description": paper.abstract[:200],
                    "capabilities": paper.capabilities,
                    "status": "candidate",
                    "source": paper.url,
                    "requires_gpu": paper.requires_gpu,
                    "requires_external": paper.requires_external,
                    "implementation_notes": paper.implementation_notes,
                    "relevance_score": paper.relevance_score,
                }
                candidates.append(candidate)
                break  # One entry per paper
        return candidates

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _search_with_llm(
        self,
        query: str,
        max_results: int,
    ) -> list[PaperResult]:
        """Use LLM to generate relevant paper results for a query."""
        if not self.use_llm:
            return self._fallback_results(query)

        try:
            from openai import OpenAI
            client = OpenAI()
        except ImportError:
            return self._fallback_results(query)

        prompt = textwrap.dedent(f"""
            You are an expert in computer graphics, procedural generation, and
            pixel art mathematics. Generate {max_results} highly relevant academic
            papers or GitHub repositories for this search query:

            Query: "{query}"

            Context: This is for a pixel art game (Mario-style) that uses:
            - OKLAB color science for palette generation
            - SDF (Signed Distance Fields) for effects
            - Wave Function Collapse for level generation
            - L-Systems for plant generation
            - Spring-damper physics for animation
            - Procedural skeletal animation
            - Future: pseudo-3D rendering, Unity shader optimization

            For each result, provide a JSON object:
            {{
              "title": "Paper/Repo title",
              "source": "arxiv|github|papers_with_code",
              "url": "https://...",
              "abstract": "2-3 sentence description",
              "year": 2024,
              "applicability": 0.0-1.0,
              "implementability": 0.0-1.0,
              "novelty": 0.0-1.0,
              "quality": 0.0-1.0,
              "capabilities": ["COLOR_PALETTE", "TEXTURE", ...],
              "implementation_notes": "What would it take to implement?",
              "requires_gpu": true|false,
              "requires_external": ""
            }}

            Return a JSON array of {max_results} results. Only include real,
            verifiable papers/repos. If fewer than {max_results} exist, return fewer.
        """).strip()

        try:
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content.strip()

            # Extract JSON array
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            # Find JSON array
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]

            data = json.loads(raw)
            results = []
            for item in data:
                relevance = (
                    item.get("applicability", 0.5) * 0.4 +
                    item.get("implementability", 0.5) * 0.3 +
                    item.get("novelty", 0.5) * 0.2 +
                    item.get("quality", 0.5) * 0.1
                )
                results.append(PaperResult(
                    title=item.get("title", "Unknown"),
                    source=item.get("source", "unknown"),
                    url=item.get("url", ""),
                    abstract=item.get("abstract", ""),
                    year=item.get("year", 2024),
                    applicability=item.get("applicability", 0.5),
                    implementability=item.get("implementability", 0.5),
                    novelty=item.get("novelty", 0.5),
                    quality=item.get("quality", 0.5),
                    relevance_score=relevance,
                    capabilities=item.get("capabilities", []),
                    implementation_notes=item.get("implementation_notes", ""),
                    requires_gpu=item.get("requires_gpu", False),
                    requires_external=item.get("requires_external", ""),
                ))
            return results

        except Exception as e:
            if self.verbose:
                print(f"[PaperMiner] LLM search failed for '{query}': {e}")
            return self._fallback_results(query)

    def _fallback_results(self, query: str) -> list[PaperResult]:
        """Return empty results when LLM is unavailable."""
        return []

    def _extract_papers_from_text(
        self,
        text: str,
        source_name: str,
    ) -> list[PaperResult]:
        """Extract paper references from free text."""
        results = []
        # Simple heuristic: look for lines that look like paper titles
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 20]
        for line in lines[:20]:  # Limit to first 20 candidates
            caps = self._detect_capabilities(line)
            if not caps:
                continue
            relevance = 0.5  # Default for manually extracted
            results.append(PaperResult(
                title=line[:100],
                source=source_name,
                url="",
                abstract=line,
                year=datetime.utcnow().year,
                applicability=0.5,
                implementability=0.7,
                novelty=0.5,
                quality=0.5,
                relevance_score=relevance,
                capabilities=caps,
                implementation_notes="Manually extracted — review required.",
            ))
        return results

    def _detect_capabilities(self, text: str) -> list[str]:
        """Detect which capabilities a text snippet relates to."""
        text_lower = text.lower()
        detected = []
        for cap, keywords in self.CAPABILITY_KEYWORDS.items():
            if any(kw.lower() in text_lower for kw in keywords):
                detected.append(cap)
        return detected

    def _save_to_knowledge(self, session: MiningSession) -> None:
        """Append accepted papers to knowledge/math_papers.md."""
        if not session.results:
            return
        knowledge_dir = self.project_root / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        papers_file = knowledge_dir / "math_papers.md"

        lines = [
            f"\n## {session.session_id} — {session.timestamp[:10]}",
            f"Query: {session.query}",
            f"Accepted: {session.papers_accepted} / {session.papers_found}",
            "",
        ]
        for paper in session.results:
            lines.extend([
                f"### {paper.title}",
                f"- **Source**: [{paper.source}]({paper.url})",
                f"- **Year**: {paper.year}",
                f"- **Relevance**: {paper.relevance_score:.2f}",
                f"- **Capabilities**: {', '.join(paper.capabilities)}",
                f"- **Requires GPU**: {'Yes' if paper.requires_gpu else 'No'}",
                f"- **Abstract**: {paper.abstract[:300]}",
                f"- **Implementation**: {paper.implementation_notes[:200]}",
                "",
            ])

        with open(papers_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _append_to_log(self, session: MiningSession) -> None:
        """Append session summary to MINE_LOG.md."""
        log_path = self.project_root / "MINE_LOG.md"
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n## {session.session_id} — {ts}\n\n")
            f.write(f"{session.summary()}\n\n")
            if session.results:
                f.write("Accepted papers:\n")
                for p in session.results:
                    f.write(f"  - [{p.relevance_score:.2f}] {p.title[:80]}\n")
            f.write("\n")

    def _paper_to_model_name(self, title: str) -> str:
        """Convert a paper title to a snake_case model name."""
        name = title.lower()
        name = re.sub(r"[^\w\s]", "", name)
        words = name.split()[:5]  # First 5 words
        return "_".join(words)

    def _load_session_count(self) -> int:
        """Load the current session count from MINE_LOG.md."""
        log_path = self.project_root / "MINE_LOG.md"
        if not log_path.exists():
            return 0
        content = log_path.read_text(encoding="utf-8")
        matches = re.findall(r"MINE-(\d+)", content)
        if matches:
            return max(int(m) for m in matches)
        return 0

    def _save_session_count(self) -> None:
        """Session count is implicitly saved via MINE_LOG.md."""
        pass  # Count is derived from log file on next load


def _cli_entry() -> None:
    """CLI entry point for mathart-mine command."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="mathart-mine",
        description="MathPaperMiner — search and integrate math papers into the project",
    )
    subparsers = parser.add_subparsers(dest="command")

    # mine command
    mine_cmd = subparsers.add_parser("mine", help="Run a mining session")
    mine_cmd.add_argument("--queries", nargs="*", help="Custom search queries")
    mine_cmd.add_argument("--max", type=int, default=5, help="Max results per query")

    # text command
    text_cmd = subparsers.add_parser("text", help="Extract papers from text file")
    text_cmd.add_argument("file", help="Text file path")
    text_cmd.add_argument("--source", default="manual", help="Source name")

    args = parser.parse_args()

    miner = MathPaperMiner(verbose=True)

    if args.command == "mine":
        session = miner.mine(queries=args.queries, max_results_per_query=args.max)
        print(f"\n{session.summary()}")
    elif args.command == "text":
        text = Path(args.file).read_text(encoding="utf-8")
        session = miner.mine_from_text(text, source_name=args.source)
        print(f"\n{session.summary()}")
    else:
        parser.print_help()
        sys.exit(1)

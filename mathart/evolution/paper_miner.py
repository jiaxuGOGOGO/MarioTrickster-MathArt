"""Math paper and repository mining for the self-evolving math-art engine.

Purpose
-------
Systematically search for, evaluate, and integrate relevant mathematical
models from academic papers and GitHub repositories into the project's math
model registry.

Search strategy
---------------
The miner now uses real external APIs as its primary data sources:

1. arXiv API — academic papers and preprints.
2. GitHub Search API — repositories and implementation references.
3. LLM fallback — only used when live APIs are disabled or unavailable.

Integration pipeline
--------------------
search → score → deduplicate → register_candidate → notify_user
                                      ↓
                            (user approves) → implement → test → promote
"""
from __future__ import annotations

import json
import math
import os
import re
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


@dataclass
class PaperResult:
    """A found paper or repository."""

    title: str
    source: str        # "arxiv", "github", "papers_with_code", "manual"
    url: str
    abstract: str
    year: int
    applicability: float
    implementability: float
    novelty: float
    quality: float
    relevance_score: float
    capabilities: list[str]
    implementation_notes: str
    requires_gpu: bool = False
    requires_external: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class MiningSession:
    """Record of a paper mining session."""

    session_id: str
    query: str
    timestamp: str
    papers_found: int
    papers_accepted: int
    papers_rejected: int
    results: list[PaperResult] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"MiningSession {self.session_id}: "
            f"found={self.papers_found}, "
            f"accepted={self.papers_accepted}, "
            f"rejected={self.papers_rejected}"
        )


class MathPaperMiner:
    """Search for and integrate relevant math papers into the project.

    Parameters
    ----------
    project_root : Path, optional
        Project root directory.
    relevance_threshold : float
        Minimum relevance score to accept a result.
    use_llm : bool
        Whether LLM fallback is allowed when live API search fails.
    use_live_apis : bool
        Whether to use real external APIs as the primary search source.
    verbose : bool
        Print progress messages.
    github_token : str, optional
        Optional GitHub token. If omitted, environment variables such as
        ``GITHUB_TOKEN``, ``GH_TOKEN`` and ``GITHUB_PAT`` are checked.
    api_timeout : float
        HTTP timeout in seconds.
    """

    ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"
    GITHUB_SEARCH_ENDPOINT = "https://api.github.com/search/repositories"
    GITHUB_API_VERSION = "2022-11-28"

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

    CAPABILITY_KEYWORDS = {
        "COLOR_PALETTE": ["palette", "color quantization", "oklab", "perceptual color", "dither"],
        "TEXTURE": ["texture synthesis", "noise", "perlin", "simplex", "wang tiles"],
        "ANIMATION": ["procedural animation", "spring", "ik", "inverse kinematics", "skeleton"],
        "SDF": ["signed distance field", "sdf", "ray marching", "distance function"],
        "PCG": ["procedural generation", "wfc", "wave function collapse", "l-system", "tilemap"],
        "SHADER_PARAMS": ["shader", "hlsl", "glsl", "rendering pipeline", "fragment"],
        "PIXEL_IMAGE": ["pixel art", "sprite", "image synthesis", "sprite generation"],
        "PSEUDO_3D": ["pseudo 3d", "isometric", "normal map", "parallax", "billboard"],
    }

    GPU_KEYWORDS = [
        "gpu",
        "cuda",
        "neural",
        "deep learning",
        "diffusion",
        "transformer",
        "pytorch",
        "tensorflow",
    ]

    EXTERNAL_KEYWORDS = {
        "pytorch": "PyTorch",
        "tensorflow": "TensorFlow",
        "unity": "Unity",
        "blender": "Blender",
        "shader graph": "Shader Graph",
        "opengl": "OpenGL",
        "vulkan": "Vulkan",
    }

    QUERY_STOPWORDS = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "using",
        "use",
        "art",
        "math",
    }

    def __init__(
        self,
        project_root: Optional[Path] = None,
        relevance_threshold: float = 0.6,
        use_llm: bool = True,
        use_live_apis: bool = True,
        verbose: bool = False,
        github_token: Optional[str] = None,
        api_timeout: float = 20.0,
    ) -> None:
        self.project_root = Path(project_root) if project_root else Path(".")
        self.relevance_threshold = relevance_threshold
        self.use_llm = use_llm
        self.use_live_apis = use_live_apis
        self.verbose = verbose
        self.github_token = github_token or self._load_github_token()
        self.api_timeout = api_timeout
        self._session_count = self._load_session_count()

    # ── Public API ─────────────────────────────────────────────────────

    def mine(
        self,
        queries: Optional[list[str]] = None,
        max_results_per_query: int = 5,
    ) -> MiningSession:
        """Run a full mining session over the given search queries."""
        self._session_count += 1
        session_id = f"MINE-{self._session_count:03d}"
        queries = queries or self.DEFAULT_QUERIES
        timestamp = datetime.utcnow().isoformat()

        if self.verbose:
            print(f"[PaperMiner] Starting session {session_id} with {len(queries)} queries")

        all_results: list[PaperResult] = []
        for query in queries:
            results = self._search_query(query, max_results_per_query)
            all_results.extend(results)

        unique_results = self._deduplicate_results(all_results)
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

        self._save_to_knowledge(session)
        self._save_session_count()
        self._append_to_log(session)

        if self.verbose:
            print(f"[PaperMiner] {session.summary()}")

        return session

    def mine_from_text(self, text: str, source_name: str = "manual") -> MiningSession:
        """Extract paper references from a free-form text block."""
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

    def generate_registry_candidates(self, session: MiningSession) -> list[dict]:
        """Convert accepted papers to math model registry candidate entries."""
        candidates: list[dict] = []
        for paper in session.results:
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
        return candidates

    # ── Search orchestration ───────────────────────────────────────────

    def _search_query(self, query: str, max_results: int) -> list[PaperResult]:
        """Search real APIs first and fall back to the LLM if necessary."""
        if self.use_live_apis:
            live_results = self._search_real_sources(query, max_results)
            if live_results:
                return live_results

        if self.use_llm:
            return self._search_with_llm(query, max_results)
        return self._fallback_results(query)

    def _search_real_sources(self, query: str, max_results: int) -> list[PaperResult]:
        """Collect results from arXiv and GitHub Search APIs."""
        results: list[PaperResult] = []
        arxiv_limit = max(1, (max_results + 1) // 2)
        github_limit = max(1, max_results // 2) if max_results > 1 else 1

        try:
            results.extend(self._search_arxiv(query, arxiv_limit))
        except Exception as exc:  # pragma: no cover - defensive logging
            if self.verbose:
                print(f"[PaperMiner] arXiv search failed for '{query}': {exc}")

        try:
            results.extend(self._search_github(query, github_limit))
        except Exception as exc:  # pragma: no cover - defensive logging
            if self.verbose:
                print(f"[PaperMiner] GitHub search failed for '{query}': {exc}")

        return results

    def _search_arxiv(self, query: str, max_results: int) -> list[PaperResult]:
        """Search arXiv via its public Atom API."""
        params = {
            "search_query": self._build_arxiv_query(query),
            "start": 0,
            "max_results": max(1, min(max_results, 20)),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        response = requests.get(
            self.ARXIV_ENDPOINT,
            params=params,
            headers={"User-Agent": self._user_agent()},
            timeout=self.api_timeout,
        )
        response.raise_for_status()

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(response.text)
        results: list[PaperResult] = []

        for entry in root.findall("atom:entry", ns):
            title = self._clean_text(entry.findtext("atom:title", default="", namespaces=ns))
            abstract = self._clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
            url = self._clean_text(entry.findtext("atom:id", default="", namespaces=ns))
            published = self._clean_text(entry.findtext("atom:published", default="", namespaces=ns))
            year = self._extract_year(published)
            capabilities = self._detect_capabilities(f"{title} {abstract} {query}")
            requires_gpu, requires_external = self._infer_requirements(f"{title} {abstract}")
            applicability = self._estimate_applicability(query, title, abstract, capabilities)
            implementability = self._estimate_implementability(requires_gpu, requires_external)
            novelty = self._estimate_novelty(capabilities, title, abstract)
            quality = self._estimate_quality("arxiv", year=year)
            relevance = self._combine_scores(applicability, implementability, novelty, quality)

            authors = [
                self._clean_text(author.findtext("atom:name", default="", namespaces=ns))
                for author in entry.findall("atom:author", ns)
            ]
            categories = [cat.attrib.get("term", "") for cat in entry.findall("atom:category", ns)]
            notes = self._build_implementation_notes(
                source="arxiv",
                capabilities=capabilities,
                requires_gpu=requires_gpu,
                requires_external=requires_external,
                metadata={"authors": authors, "categories": categories},
            )

            results.append(
                PaperResult(
                    title=title or "Unknown arXiv entry",
                    source="arxiv",
                    url=url,
                    abstract=abstract,
                    year=year,
                    applicability=applicability,
                    implementability=implementability,
                    novelty=novelty,
                    quality=quality,
                    relevance_score=relevance,
                    capabilities=capabilities,
                    implementation_notes=notes,
                    requires_gpu=requires_gpu,
                    requires_external=requires_external,
                    metadata={"authors": authors, "categories": categories},
                )
            )

        return results

    def _search_github(self, query: str, max_results: int) -> list[PaperResult]:
        """Search GitHub repositories through the REST search API."""
        params = {
            "q": self._build_github_query(query),
            "sort": "stars",
            "order": "desc",
            "per_page": max(1, min(max_results, 20)),
        }
        response = requests.get(
            self.GITHUB_SEARCH_ENDPOINT,
            params=params,
            headers=self._github_headers(),
            timeout=self.api_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", [])
        results: list[PaperResult] = []

        for item in items:
            title = item.get("full_name") or item.get("name") or "Unknown repository"
            topics = item.get("topics") or []
            language = item.get("language") or ""
            abstract = self._clean_text(
                " ".join(
                    part for part in [item.get("description", ""), language, " ".join(topics)] if part
                )
            )
            updated_at = item.get("updated_at", "")
            year = self._extract_year(updated_at)
            text_blob = f"{title} {abstract} {query}"
            capabilities = self._detect_capabilities(text_blob)
            requires_gpu, requires_external = self._infer_requirements(text_blob)
            applicability = self._estimate_applicability(query, title, abstract, capabilities)
            implementability = self._estimate_implementability(requires_gpu, requires_external)
            novelty = self._estimate_novelty(capabilities, title, abstract)
            quality = self._estimate_quality(
                "github",
                year=year,
                metadata={
                    "stars": item.get("stargazers_count", 0),
                    "forks": item.get("forks_count", 0),
                    "archived": bool(item.get("archived", False)),
                },
            )
            relevance = self._combine_scores(applicability, implementability, novelty, quality)
            notes = self._build_implementation_notes(
                source="github",
                capabilities=capabilities,
                requires_gpu=requires_gpu,
                requires_external=requires_external,
                metadata={
                    "language": language,
                    "stars": item.get("stargazers_count", 0),
                    "forks": item.get("forks_count", 0),
                    "topics": topics,
                },
            )

            results.append(
                PaperResult(
                    title=title,
                    source="github",
                    url=item.get("html_url", ""),
                    abstract=abstract,
                    year=year,
                    applicability=applicability,
                    implementability=implementability,
                    novelty=novelty,
                    quality=quality,
                    relevance_score=relevance,
                    capabilities=capabilities,
                    implementation_notes=notes,
                    requires_gpu=requires_gpu,
                    requires_external=requires_external,
                    metadata={
                        "language": language,
                        "stars": item.get("stargazers_count", 0),
                        "forks": item.get("forks_count", 0),
                        "topics": topics,
                    },
                )
            )

        return results

    def _search_with_llm(self, query: str, max_results: int) -> list[PaperResult]:
        """Use an LLM fallback to propose verifiable search candidates."""
        if not self.use_llm:
            return self._fallback_results(query)

        try:
            from openai import OpenAI

            client = OpenAI()
        except ImportError:
            return self._fallback_results(query)

        prompt = textwrap.dedent(
            f"""
            You are helping curate search leads for a math-driven pixel-art engine.
            Real APIs are preferred, but they were unavailable for this query.
            Produce up to {max_results} real, verifiable papers or GitHub repositories
            relevant to the following topic:

            Query: {query}

            Return a JSON array. Each item must include:
            - title
            - source (arxiv|github|papers_with_code)
            - url
            - abstract
            - year
            - applicability
            - implementability
            - novelty
            - quality
            - capabilities
            - implementation_notes
            - requires_gpu
            - requires_external
            """
        ).strip()

        try:
            response = client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1800,
            )
            raw = response.choices[0].message.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start >= 0 and end > start:
                raw = raw[start:end]
            data = json.loads(raw)
        except Exception as exc:
            if self.verbose:
                print(f"[PaperMiner] LLM fallback failed for '{query}': {exc}")
            return self._fallback_results(query)

        results: list[PaperResult] = []
        for item in data[:max_results]:
            capabilities = item.get("capabilities") or self._detect_capabilities(
                f"{item.get('title', '')} {item.get('abstract', '')}"
            )
            requires_gpu = bool(item.get("requires_gpu", False))
            requires_external = item.get("requires_external", "")
            applicability = float(item.get("applicability", 0.5))
            implementability = float(item.get("implementability", 0.5))
            novelty = float(item.get("novelty", 0.5))
            quality = float(item.get("quality", 0.5))
            relevance = self._combine_scores(applicability, implementability, novelty, quality)
            results.append(
                PaperResult(
                    title=item.get("title", "Unknown"),
                    source=item.get("source", "unknown"),
                    url=item.get("url", ""),
                    abstract=item.get("abstract", ""),
                    year=int(item.get("year", datetime.utcnow().year)),
                    applicability=applicability,
                    implementability=implementability,
                    novelty=novelty,
                    quality=quality,
                    relevance_score=relevance,
                    capabilities=capabilities,
                    implementation_notes=item.get("implementation_notes", ""),
                    requires_gpu=requires_gpu,
                    requires_external=requires_external,
                )
            )
        return results

    # ── Scoring and heuristics ─────────────────────────────────────────

    def _detect_capabilities(self, text: str) -> list[str]:
        """Detect which capability buckets a text snippet maps to."""
        text_lower = text.lower()
        detected: list[str] = []
        for capability, keywords in self.CAPABILITY_KEYWORDS.items():
            if any(keyword in text_lower for keyword in keywords):
                detected.append(capability)
        return detected

    def _infer_requirements(self, text: str) -> tuple[bool, str]:
        """Infer GPU or external tool requirements from free text."""
        lowered = text.lower()
        requires_gpu = any(keyword in lowered for keyword in self.GPU_KEYWORDS)
        external = [name for keyword, name in self.EXTERNAL_KEYWORDS.items() if keyword in lowered]
        requires_external = ", ".join(sorted(set(external)))
        return requires_gpu, requires_external

    def _estimate_applicability(
        self,
        query: str,
        title: str,
        abstract: str,
        capabilities: list[str],
    ) -> float:
        """Estimate how directly applicable a result is to the project."""
        query_tokens = set(self._extract_keywords(query))
        result_tokens = set(self._extract_keywords(f"{title} {abstract}"))
        overlap = len(query_tokens & result_tokens) / max(len(query_tokens), 1)
        cap_bonus = min(0.28, 0.09 * len(set(capabilities)))
        pixel_bonus = 0.12 if any(token in result_tokens for token in {"pixel", "sprite", "tilemap", "game"}) else 0.0
        score = 0.35 + 0.35 * overlap + cap_bonus + pixel_bonus
        return self._clamp(score)

    def _estimate_implementability(self, requires_gpu: bool, requires_external: str) -> float:
        """Estimate how practical the result is to implement locally on CPU."""
        score = 0.9
        if requires_external:
            score -= 0.18
        if requires_gpu:
            score -= 0.28
        return self._clamp(score)

    def _estimate_novelty(self, capabilities: list[str], title: str, abstract: str) -> float:
        """Estimate how much new capability the result might add."""
        lowered = f"{title} {abstract}".lower()
        score = 0.45 + 0.08 * len(set(capabilities))
        if any(keyword in lowered for keyword in ["differentiable", "bayesian", "neural", "pseudo 3d"]):
            score += 0.12
        return self._clamp(score)

    def _estimate_quality(
        self,
        source: str,
        year: int,
        metadata: Optional[dict] = None,
    ) -> float:
        """Estimate quality from source-specific public metadata."""
        metadata = metadata or {}
        current_year = datetime.utcnow().year

        if source == "github":
            stars = float(metadata.get("stars", 0))
            forks = float(metadata.get("forks", 0))
            archived = bool(metadata.get("archived", False))
            score = 0.3 + min(0.45, math.log10(stars + 1) / 4.0) + min(0.15, math.log10(forks + 1) / 10.0)
            if not archived:
                score += 0.1
            return self._clamp(score)

        age = max(0, current_year - year)
        if age <= 1:
            return 0.82
        if age <= 3:
            return 0.74
        if age <= 6:
            return 0.66
        return 0.58

    def _combine_scores(
        self,
        applicability: float,
        implementability: float,
        novelty: float,
        quality: float,
    ) -> float:
        """Combine the four axes into a single relevance score."""
        return self._clamp(
            applicability * 0.4
            + implementability * 0.3
            + novelty * 0.2
            + quality * 0.1
        )

    def _build_implementation_notes(
        self,
        source: str,
        capabilities: list[str],
        requires_gpu: bool,
        requires_external: str,
        metadata: dict,
    ) -> str:
        """Build a concise implementation note for logs and registry entries."""
        parts: list[str] = []
        if capabilities:
            parts.append(f"Targets capabilities: {', '.join(capabilities)}.")
        if source == "github":
            language = metadata.get("language")
            stars = metadata.get("stars")
            if language:
                parts.append(f"Reference implementation language: {language}.")
            if stars is not None:
                parts.append(f"Repository popularity signal: {stars} stars.")
        else:
            authors = metadata.get("authors") or []
            if authors:
                parts.append(f"Paper authors: {', '.join(authors[:3])}.")
            categories = metadata.get("categories") or []
            if categories:
                parts.append(f"arXiv categories: {', '.join(categories[:3])}.")
        if requires_external:
            parts.append(f"May require external tooling: {requires_external}.")
        if requires_gpu:
            parts.append("Likely GPU-accelerated or ML-heavy; local CPU-only adaptation may be required.")
        if not parts:
            parts.append("Promising lead; implementation review required.")
        return " ".join(parts)

    # ── Extraction and persistence ─────────────────────────────────────

    def _extract_papers_from_text(self, text: str, source_name: str) -> list[PaperResult]:
        """Extract paper references from free text using heuristics."""
        results: list[PaperResult] = []
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 20]
        for line in lines[:20]:
            capabilities = self._detect_capabilities(line)
            if not capabilities:
                continue
            relevance = self._combine_scores(0.5, 0.7, 0.5, 0.5)
            results.append(
                PaperResult(
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
                    capabilities=capabilities,
                    implementation_notes="Manually extracted — review required.",
                )
            )
        return results

    def _save_to_knowledge(self, session: MiningSession) -> None:
        """Append accepted results to knowledge/math_papers.md."""
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
            lines.extend(
                [
                    f"### {paper.title}",
                    f"- **Source**: [{paper.source}]({paper.url})",
                    f"- **Year**: {paper.year}",
                    f"- **Relevance**: {paper.relevance_score:.2f}",
                    f"- **Capabilities**: {', '.join(paper.capabilities)}",
                    f"- **Requires GPU**: {'Yes' if paper.requires_gpu else 'No'}",
                    f"- **Requires External**: {paper.requires_external or 'No'}",
                    f"- **Abstract**: {paper.abstract[:300]}",
                    f"- **Implementation**: {paper.implementation_notes[:240]}",
                    "",
                ]
            )

        with open(papers_file, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _append_to_log(self, session: MiningSession) -> None:
        """Append the session summary to MINE_LOG.md."""
        log_path = self.project_root / "MINE_LOG.md"
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n## {session.session_id} — {timestamp}\n\n")
            handle.write(f"{session.summary()}\n\n")
            if session.results:
                handle.write("Accepted papers:\n")
                for result in session.results:
                    handle.write(f"  - [{result.relevance_score:.2f}] {result.title[:80]}\n")
            handle.write("\n")

    # ── Low-level helpers ──────────────────────────────────────────────

    def _deduplicate_results(self, results: list[PaperResult]) -> list[PaperResult]:
        """Deduplicate results by URL, falling back to normalized title."""
        unique: list[PaperResult] = []
        seen_keys: set[str] = set()
        for result in results:
            key = result.url.strip() or self._paper_to_model_name(result.title)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(result)
        return unique

    def _build_arxiv_query(self, query: str) -> str:
        """Convert a free-form query into an arXiv search expression."""
        tokens = self._extract_keywords(query)[:6]
        if not tokens:
            return f"all:{query}"
        return " AND ".join(f"all:{token}" for token in tokens)

    def _build_github_query(self, query: str) -> str:
        """Convert a free-form query into a GitHub repository search query."""
        cleaned = " ".join(self._extract_keywords(query)[:8]) or query
        return f"{cleaned} archived:false"

    def _github_headers(self) -> dict[str, str]:
        """Build GitHub REST headers with optional token authentication."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self._user_agent(),
            "X-GitHub-Api-Version": self.GITHUB_API_VERSION,
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _user_agent(self) -> str:
        return "MarioTrickster-MathArt/0.11.0"

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract normalized keywords from a free-form query."""
        tokens = re.findall(r"[a-zA-Z0-9_+-]+", text.lower())
        return [
            token
            for token in tokens
            if len(token) >= 3 and token not in self.QUERY_STOPWORDS
        ]

    def _clean_text(self, text: Optional[str]) -> str:
        """Normalize whitespace inside text content."""
        return re.sub(r"\s+", " ", (text or "")).strip()

    def _extract_year(self, value: str) -> int:
        """Extract a year from an ISO date or free text value."""
        match = re.search(r"(19|20)\d{2}", value or "")
        if match:
            return int(match.group(0))
        return datetime.utcnow().year

    def _paper_to_model_name(self, title: str) -> str:
        """Convert a paper title to a snake_case model name."""
        name = title.lower()
        name = re.sub(r"[^\w\s]", "", name)
        words = name.split()[:5]
        return "_".join(words)

    def _load_github_token(self) -> Optional[str]:
        """Load a GitHub token from common environment variables."""
        for env_name in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
            value = os.getenv(env_name)
            if value:
                return value
        return None

    def _load_session_count(self) -> int:
        """Load the current session count from MINE_LOG.md."""
        log_path = self.project_root / "MINE_LOG.md"
        if not log_path.exists():
            return 0
        content = log_path.read_text(encoding="utf-8")
        matches = re.findall(r"MINE-(\d+)", content)
        if matches:
            return max(int(match) for match in matches)
        return 0

    def _save_session_count(self) -> None:
        """Session count is derived from the log file on next load."""
        return None

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def _fallback_results(self, query: str) -> list[PaperResult]:
        """Return an empty result list when no search path is available."""
        if self.verbose:
            print(f"[PaperMiner] No results available for query '{query}'")
        return []


def _cli_entry() -> None:
    """CLI entry point for the ``mathart-mine`` command."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="mathart-mine",
        description="MathPaperMiner — search and integrate math papers into the project",
    )
    subparsers = parser.add_subparsers(dest="command")

    mine_cmd = subparsers.add_parser("mine", help="Run a mining session")
    mine_cmd.add_argument("--queries", nargs="*", help="Custom search queries")
    mine_cmd.add_argument("--max", type=int, default=5, help="Max results per query")
    mine_cmd.add_argument(
        "--no-live-api",
        action="store_true",
        help="Disable real HTTP APIs and use fallback or LLM-only behaviour",
    )

    text_cmd = subparsers.add_parser("text", help="Extract papers from text file")
    text_cmd.add_argument("file", help="Text file path")
    text_cmd.add_argument("--source", default="manual", help="Source name")

    args = parser.parse_args()

    miner = MathPaperMiner(verbose=True, use_live_apis=not getattr(args, "no_live_api", False))

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

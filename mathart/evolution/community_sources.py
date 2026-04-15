"""Community source extensions for MathPaperMiner (TASK-017).

Adds additional search sources beyond arXiv and GitHub:

1. **Papers with Code** — Searches the Papers with Code API for papers
   with linked implementations, which are more immediately actionable.
2. **Shadertoy** — Searches Shadertoy for SDF/shader techniques that
   map directly to the project's SDF rendering pipeline.
3. **Claude Code / LLM advisor** — Optional AI-assisted code review
   and evolution suggestions (requires API key configuration).

Design principles:
  - Each source is a standalone class implementing a common interface
  - Sources are opt-in: if an API key or endpoint is unavailable, the
    source is silently skipped
  - Results use the same PaperResult dataclass as the core miner
  - No new heavy dependencies — only stdlib + requests
  - External AI APIs (Claude, etc.) are optional accelerators, not
    requirements — the system works without them

Usage:
    from mathart.evolution.community_sources import CommunitySourceRegistry
    registry = CommunitySourceRegistry(project_root=Path("."))
    results = registry.search_all("procedural pixel art SDF")
"""
from __future__ import annotations

import json
import os
import re
import textwrap
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .paper_miner import PaperResult


class CommunitySource(ABC):
    """Base class for community search sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name."""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether this source is currently usable."""
        ...

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> list[PaperResult]:
        """Search this source and return scored results."""
        ...


class PapersWithCodeSource(CommunitySource):
    """Search Papers with Code for papers with linked implementations.

    Papers with Code provides a free API that returns papers along with
    their GitHub implementations, making results more actionable than
    raw arXiv results.

    API docs: https://paperswithcode.com/api/v1/docs/
    """

    ENDPOINT = "https://paperswithcode.com/api/v1/papers/"
    SEARCH_ENDPOINT = "https://paperswithcode.com/api/v1/search/"

    CAPABILITY_KEYWORDS = {
        "COLOR_PALETTE": ["palette", "color", "quantization"],
        "TEXTURE": ["texture", "noise", "procedural"],
        "ANIMATION": ["animation", "motion", "skeleton"],
        "SDF": ["signed distance", "sdf", "ray marching"],
        "PCG": ["procedural generation", "wfc", "wave function"],
        "PIXEL_IMAGE": ["pixel art", "sprite", "image synthesis"],
        "PSEUDO_3D": ["3d", "normal map", "depth"],
    }

    def __init__(self, timeout: float = 15.0, verbose: bool = False):
        self.timeout = timeout
        self.verbose = verbose

    @property
    def name(self) -> str:
        return "papers_with_code"

    @property
    def is_available(self) -> bool:
        return True  # Free API, no key required

    def search(self, query: str, max_results: int = 5) -> list[PaperResult]:
        """Search Papers with Code API."""
        try:
            response = requests.get(
                self.SEARCH_ENDPOINT,
                params={"q": query, "page": 1, "items_per_page": max_results},
                headers={"User-Agent": "MarioTrickster-MathArt/1.0"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            if self.verbose:
                print(f"[PapersWithCode] Search failed: {exc}")
            return []

        results = []
        items = data.get("results", [])
        for item in items[:max_results]:
            title = item.get("paper", {}).get("title", "Unknown")
            abstract = item.get("paper", {}).get("abstract", "")
            url = item.get("paper", {}).get("url_abs", "")
            year = self._extract_year(item.get("paper", {}).get("published", ""))

            text_blob = f"{title} {abstract} {query}"
            capabilities = self._detect_capabilities(text_blob)
            has_code = bool(item.get("repository", {}).get("url", ""))
            repo_url = item.get("repository", {}).get("url", "")

            # Score higher if implementation exists
            applicability = 0.5 + (0.2 if has_code else 0.0)
            implementability = 0.8 if has_code else 0.5
            novelty = 0.5 + 0.08 * len(capabilities)
            quality = 0.6
            relevance = (applicability + implementability + novelty + quality) / 4.0

            notes = "Papers with Code entry. "
            if has_code:
                notes += f"Implementation: {repo_url}. "
            notes += f"Capabilities: {', '.join(capabilities) or 'general'}."

            results.append(PaperResult(
                title=title,
                source="papers_with_code",
                url=url or f"https://paperswithcode.com/search?q={query}",
                abstract=abstract[:500],
                year=year,
                applicability=applicability,
                implementability=implementability,
                novelty=novelty,
                quality=quality,
                relevance_score=min(1.0, relevance),
                capabilities=capabilities,
                implementation_notes=notes,
                requires_gpu=self._check_gpu(text_blob),
                requires_external="",
                metadata={"has_code": has_code, "repo_url": repo_url},
            ))

        return results

    def _detect_capabilities(self, text: str) -> list[str]:
        text_lower = text.lower()
        return [
            cap for cap, keywords in self.CAPABILITY_KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)
        ]

    def _check_gpu(self, text: str) -> bool:
        gpu_kw = ["gpu", "cuda", "neural", "deep learning", "pytorch", "tensorflow"]
        return any(kw in text.lower() for kw in gpu_kw)

    def _extract_year(self, date_str: str) -> int:
        if not date_str:
            return datetime.utcnow().year
        match = re.search(r"(\d{4})", date_str)
        return int(match.group(1)) if match else datetime.utcnow().year


class ShadertoySource(CommunitySource):
    """Search Shadertoy for SDF and shader techniques.

    Shadertoy shaders often contain SDF techniques, noise functions,
    and procedural generation methods that map directly to this project's
    SDF rendering pipeline.

    Note: Shadertoy API requires an app key. If not configured,
    this source is silently disabled.

    Get an API key at: https://www.shadertoy.com/howto#q1
    Set environment variable: SHADERTOY_API_KEY
    """

    ENDPOINT = "https://www.shadertoy.com/api/v1/shaders/query"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
        verbose: bool = False,
    ):
        self.api_key = api_key or os.environ.get("SHADERTOY_API_KEY", "")
        self.timeout = timeout
        self.verbose = verbose

    @property
    def name(self) -> str:
        return "shadertoy"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 5) -> list[PaperResult]:
        """Search Shadertoy API for relevant shaders."""
        if not self.is_available:
            if self.verbose:
                print("[Shadertoy] API key not configured — skipping")
            return []

        try:
            # Shadertoy search uses path-based query
            url = f"{self.ENDPOINT}/{query}"
            response = requests.get(
                url,
                params={"key": self.api_key, "num": max_results},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            if self.verbose:
                print(f"[Shadertoy] Search failed: {exc}")
            return []

        results = []
        shader_ids = data.get("Results", [])[:max_results]

        for shader_id in shader_ids:
            try:
                shader_data = self._fetch_shader(shader_id)
                if shader_data:
                    results.append(shader_data)
            except Exception:
                continue

        return results

    def _fetch_shader(self, shader_id: str) -> Optional[PaperResult]:
        """Fetch details for a single shader."""
        url = f"https://www.shadertoy.com/api/v1/shaders/{shader_id}"
        response = requests.get(
            url,
            params={"key": self.api_key},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        shader = data.get("Shader", {})
        info = shader.get("info", {})
        title = info.get("name", "Unknown Shader")
        description = info.get("description", "")
        username = info.get("username", "")
        shader_url = f"https://www.shadertoy.com/view/{shader_id}"

        # Extract code for capability detection
        code = ""
        for rp in shader.get("renderpass", []):
            code += rp.get("code", "") + " "

        capabilities = self._detect_shader_capabilities(code, description)

        return PaperResult(
            title=f"Shadertoy: {title} by {username}",
            source="shadertoy",
            url=shader_url,
            abstract=description[:500],
            year=datetime.utcnow().year,
            applicability=0.6 if capabilities else 0.3,
            implementability=0.7,  # GLSL → Python SDF is well-understood
            novelty=0.5 + 0.1 * len(capabilities),
            quality=0.5,
            relevance_score=0.6 if capabilities else 0.35,
            capabilities=capabilities,
            implementation_notes="Shadertoy shader. GLSL code available for SDF/noise porting.",
            requires_gpu=False,  # We port to CPU Python
            requires_external="",
            metadata={"shader_id": shader_id, "username": username},
        )

    def _detect_shader_capabilities(self, code: str, description: str) -> list[str]:
        text = f"{code} {description}".lower()
        caps = []
        if any(kw in text for kw in ["sdf", "distance", "sdbox", "sdcircle"]):
            caps.append("SDF")
        if any(kw in text for kw in ["noise", "fbm", "perlin", "simplex"]):
            caps.append("TEXTURE")
        if any(kw in text for kw in ["palette", "color"]):
            caps.append("COLOR_PALETTE")
        if any(kw in text for kw in ["animate", "time", "itime"]):
            caps.append("ANIMATION")
        return caps


class LLMAdvisorSource(CommunitySource):
    """Optional AI-assisted code review and evolution advisor.

    Uses Claude Code, GPT, or other LLM APIs to provide:
    - Code review suggestions for evolution quality
    - Mathematical technique recommendations
    - Parameter tuning advice

    This is an **optional accelerator** — the system works without it.
    When available, it enhances the quality of evolution decisions.

    Supported providers (checked in order):
    1. ANTHROPIC_API_KEY → Claude
    2. OPENAI_API_KEY → GPT (already available in project)

    Configuration:
    - Set ANTHROPIC_API_KEY for Claude Code integration
    - Or use existing OPENAI_API_KEY for GPT-based advice
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._provider = self._detect_provider()

    @property
    def name(self) -> str:
        return f"llm_advisor ({self._provider or 'none'})"

    @property
    def is_available(self) -> bool:
        return self._provider is not None

    def search(self, query: str, max_results: int = 3) -> list[PaperResult]:
        """Use LLM to suggest relevant techniques for the query."""
        if not self.is_available:
            return []

        try:
            if self._provider == "openai":
                return self._search_openai(query, max_results)
            elif self._provider == "anthropic":
                return self._search_anthropic(query, max_results)
        except Exception as exc:
            if self.verbose:
                print(f"[LLMAdvisor] {self._provider} search failed: {exc}")
        return []

    def review_evolution_quality(
        self,
        code_snippet: str,
        context: str = "",
    ) -> Optional[dict]:
        """Ask the LLM to review evolution code quality.

        Returns a dict with 'suggestions', 'quality_score', 'risks'.
        Returns None if LLM is not available.
        """
        if not self.is_available:
            return None

        prompt = textwrap.dedent(f"""
            Review this math-art evolution code for quality and suggest improvements.
            Focus on: mathematical correctness, numerical stability, performance.
            Context: {context}

            Code:
            ```python
            {code_snippet[:2000]}
            ```

            Return JSON with: suggestions (list[str]), quality_score (0-1), risks (list[str])
        """).strip()

        try:
            if self._provider == "openai":
                return self._call_openai_json(prompt)
            elif self._provider == "anthropic":
                return self._call_anthropic_json(prompt)
        except Exception as exc:
            if self.verbose:
                print(f"[LLMAdvisor] Review failed: {exc}")
        return None

    def _detect_provider(self) -> Optional[str]:
        """Detect which LLM provider is available."""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        return None

    def _search_openai(self, query: str, max_results: int) -> list[PaperResult]:
        from openai import OpenAI
        client = OpenAI()

        prompt = textwrap.dedent(f"""
            You are helping curate techniques for a math-driven pixel-art engine.
            Suggest up to {max_results} real, verifiable papers, techniques, or
            GitHub repositories relevant to: {query}

            Focus on: SDF rendering, procedural generation, pixel art, OKLAB color,
            skeletal animation, noise functions, WFC tilemap generation.

            Return a JSON array. Each item must include:
            - title, source (arxiv|github|technique), url, abstract, year
            - applicability (0-1), implementability (0-1), capabilities (list)
        """).strip()

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
        )
        raw = response.choices[0].message.content.strip()
        return self._parse_llm_results(raw, max_results)

    def _search_anthropic(self, query: str, max_results: int) -> list[PaperResult]:
        """Search using Anthropic Claude API."""
        import anthropic
        client = anthropic.Anthropic()

        prompt = textwrap.dedent(f"""
            You are helping curate techniques for a math-driven pixel-art engine.
            Suggest up to {max_results} real, verifiable papers or techniques
            relevant to: {query}

            Return a JSON array with: title, source, url, abstract, year,
            applicability (0-1), implementability (0-1), capabilities (list)
        """).strip()

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        return self._parse_llm_results(raw, max_results)

    def _parse_llm_results(self, raw: str, max_results: int) -> list[PaperResult]:
        """Parse LLM JSON response into PaperResult list."""
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        results = []
        for item in data[:max_results]:
            capabilities = item.get("capabilities", [])
            applicability = float(item.get("applicability", 0.5))
            implementability = float(item.get("implementability", 0.5))
            relevance = (applicability + implementability + 0.5) / 3.0

            results.append(PaperResult(
                title=item.get("title", "Unknown"),
                source=item.get("source", "llm_advisor"),
                url=item.get("url", ""),
                abstract=item.get("abstract", "")[:500],
                year=int(item.get("year", datetime.utcnow().year)),
                applicability=applicability,
                implementability=implementability,
                novelty=0.5,
                quality=0.5,
                relevance_score=min(1.0, relevance),
                capabilities=capabilities,
                implementation_notes="LLM-suggested technique. Verify before implementing.",
                requires_gpu=False,
                requires_external="",
                metadata={"provider": self._provider},
            ))
        return results

    def _call_openai_json(self, prompt: str) -> Optional[dict]:
        from openai import OpenAI
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)

    def _call_anthropic_json(self, prompt: str) -> Optional[dict]:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)


class CommunitySourceRegistry:
    """Registry of all community search sources.

    Manages multiple search sources and provides a unified search interface.
    Sources that are not available (missing API keys, etc.) are silently skipped.
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        verbose: bool = False,
        enable_shadertoy: bool = True,
        enable_llm: bool = True,
    ):
        self.project_root = Path(project_root) if project_root else Path(".")
        self.verbose = verbose
        self._sources: list[CommunitySource] = []

        # Always add Papers with Code (free, no key needed)
        self._sources.append(PapersWithCodeSource(verbose=verbose))

        # Conditionally add Shadertoy
        if enable_shadertoy:
            self._sources.append(ShadertoySource(verbose=verbose))

        # Conditionally add LLM advisor
        if enable_llm:
            self._sources.append(LLMAdvisorSource(verbose=verbose))

    @property
    def available_sources(self) -> list[str]:
        """List names of currently available sources."""
        return [s.name for s in self._sources if s.is_available]

    def search_all(
        self,
        query: str,
        max_results_per_source: int = 5,
    ) -> list[PaperResult]:
        """Search all available sources and merge results."""
        all_results: list[PaperResult] = []

        for source in self._sources:
            if not source.is_available:
                if self.verbose:
                    print(f"[CommunityRegistry] Skipping {source.name} (not available)")
                continue

            try:
                results = source.search(query, max_results_per_source)
                all_results.extend(results)
                if self.verbose:
                    print(f"[CommunityRegistry] {source.name}: {len(results)} results")
            except Exception as exc:
                if self.verbose:
                    print(f"[CommunityRegistry] {source.name} failed: {exc}")

        # Sort by relevance score descending
        all_results.sort(key=lambda r: r.relevance_score, reverse=True)
        return all_results

    def get_llm_advisor(self) -> Optional[LLMAdvisorSource]:
        """Get the LLM advisor source if available."""
        for source in self._sources:
            if isinstance(source, LLMAdvisorSource) and source.is_available:
                return source
        return None

    def status_report(self) -> str:
        """Generate a status report of all community sources."""
        lines = [
            "Community Source Status:",
            f"  Total sources: {len(self._sources)}",
            f"  Available: {len(self.available_sources)}",
            "",
        ]
        for source in self._sources:
            status = "ACTIVE" if source.is_available else "INACTIVE"
            lines.append(f"  [{status}] {source.name}")

        # Configuration hints for inactive sources
        inactive = [s for s in self._sources if not s.is_available]
        if inactive:
            lines.append("")
            lines.append("  To activate inactive sources:")
            for source in inactive:
                if isinstance(source, ShadertoySource):
                    lines.append("    - Shadertoy: Set SHADERTOY_API_KEY env var")
                    lines.append("      Get key at: https://www.shadertoy.com/howto#q1")
                elif isinstance(source, LLMAdvisorSource):
                    lines.append("    - LLM Advisor: Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
                    lines.append("      Claude: https://console.anthropic.com/")
                    lines.append("      OpenAI: Already configured in project")

        return "\n".join(lines)

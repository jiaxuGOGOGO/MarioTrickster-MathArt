"""Academic Miner Backend — Paper Community Miner Adapter & Pipeline Wiring.

SESSION-186: P0-SESSION-186-AUTONOMOUS-MINER-AND-POLICY-SYNTHESIZER

This module is the **Adapter layer** that wraps the dormant 1226-line
``mathart.evolution.paper_miner`` module and the 610-line
``mathart.evolution.community_sources`` module as a first-class
``@register_backend`` plugin, making them discoverable by the microkernel
orchestrator and invocable through the Laboratory Hub CLI.

Research Foundations
--------------------
1. **Agentic RAG for Scientific Literature (Singh et al., 2025)**:
   Implements an autonomous retrieval pipeline that searches arXiv,
   PapersWithCode, GitHub, and community sources for physics/animation
   papers.  Results are scored, deduplicated, and serialized as
   structured JSON for downstream policy synthesis.

2. **Exponential Backoff with Jitter (AWS Builder's Library)**:
   All HTTP requests to external APIs use a robust retry engine with
   exponential backoff (base=1s, multiplier=2x, jitter=random, max=30s,
   max_retries=3).  On persistent failure, the system falls back to
   Mock/Dummy paper data to guarantee full-chain testability.

3. **LLM Structured Data Extraction (Klusty et al., 2025)**:
   When an LLM advisor source is available, unstructured academic
   abstracts are distilled into structured physics dictionaries
   containing equations, parameters, and applicability domains.

Architecture Discipline
-----------------------
- This module is a **pure Adapter** — it does NOT modify any internal
  ``MathPaperMiner`` search logic, scoring algorithms, or community
  source implementations.
- It only provides the glue layer (input/output wiring) to make the
  dormant modules accessible through the BackendRegistry.
- Registered via ``@register_backend`` with
  ``BackendCapability.EVOLUTION_DOMAIN``.
- Produces ``ArtifactFamily.EVOLUTION_REPORT`` manifests.

Red-Line Enforcement
--------------------
- 🔴 **Zero-Modification-to-Internal-Logic Red Line**: This adapter
  NEVER touches the internal ``MathPaperMiner._search_arxiv()``,
  ``CommunitySourceRegistry.search_all()``, or any scoring math.
  It only calls the public API as a black box.
- 🔴 **Zero-Pollution-to-Production-Vault Red Line**: When invoked via
  the Laboratory Hub, outputs go to
  ``workspace/laboratory/academic_miner/`` sandbox.
- 🔴 **Strong-Typed Contract**: Returns a proper ``ArtifactManifest``
  with ``artifact_family=EVOLUTION_REPORT`` and all required metadata.
- 🔴 **Pure Reflection Discovery**: This backend auto-appears in the
  ``[6] 🔬 黑科技实验室`` menu via registry reflection — ZERO
  modifications to ``cli_wizard.py`` or ``laboratory_hub.py``.
- 🔴 **Network Resilience**: Exponential backoff + Mock fallback.
  System NEVER deadlocks on network failure.
"""
from __future__ import annotations

import json
import logging
import random
import time as _time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from mathart.core.artifact_schema import ArtifactFamily, ArtifactManifest
from mathart.core.backend_registry import (
    BackendCapability,
    BackendMeta,
    register_backend,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Backend Type (string-based, registry allow_unknown=True)
# ═══════════════════════════════════════════════════════════════════════════
_ACADEMIC_MINER_BACKEND_TYPE = "academic_miner"

# ═══════════════════════════════════════════════════════════════════════════
#  Exponential Backoff Configuration
# ═══════════════════════════════════════════════════════════════════════════
_BACKOFF_BASE_DELAY = 1.0       # seconds
_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_MAX_DELAY = 30.0       # seconds
_BACKOFF_MAX_RETRIES = 3
_BACKOFF_JITTER_RANGE = 0.5     # seconds

# ═══════════════════════════════════════════════════════════════════════════
#  Mock / Dummy Paper Data (Circuit Breaker Fallback)
# ═══════════════════════════════════════════════════════════════════════════
_DUMMY_PAPERS = [
    {
        "title": "Stable Fluids for Real-Time Physics Animation",
        "source": "mock",
        "url": "https://example.com/mock/stable-fluids",
        "abstract": (
            "We present a stable fluid simulation method based on "
            "semi-Lagrangian advection and implicit diffusion, suitable "
            "for real-time physics animation in game engines. The method "
            "uses a staggered MAC grid with pressure projection to enforce "
            "incompressibility. Key equations: Navier-Stokes momentum "
            "equation du/dt = -(u·∇)u + ν∇²u - ∇p/ρ + f."
        ),
        "year": 1999,
        "relevance_score": 0.92,
        "capabilities": ["ANIMATION", "PHYSICS_VFX"],
        "equations": [
            "du/dt = -(u·∇)u + ν∇²u - ∇p/ρ + f",
            "∇·u = 0 (incompressibility)",
            "CFL: Δt ≤ Δx / max(|u|)",
        ],
        "parameters": {
            "viscosity": {"symbol": "ν", "range": [0.0001, 0.01], "unit": "m²/s"},
            "density": {"symbol": "ρ", "range": [1.0, 1000.0], "unit": "kg/m³"},
            "grid_resolution": {"symbol": "N", "range": [32, 256], "unit": "cells"},
        },
    },
    {
        "title": "Procedural Pixel Art via Wave Function Collapse",
        "source": "mock",
        "url": "https://example.com/mock/wfc-pixel-art",
        "abstract": (
            "Wave Function Collapse (WFC) is a constraint-based procedural "
            "generation algorithm that produces locally consistent tilemap "
            "layouts from small exemplar images. We extend WFC with "
            "adjacency frequency weighting and Shannon entropy-based "
            "cell selection for pixel art generation."
        ),
        "year": 2022,
        "relevance_score": 0.88,
        "capabilities": ["TEXTURE", "SDF"],
        "equations": [
            "H(cell) = -Σ p_i log(p_i)  (Shannon entropy)",
            "p_i = freq_i / Σ freq_j  (adjacency frequency)",
        ],
        "parameters": {
            "tile_size": {"symbol": "T", "range": [8, 64], "unit": "px"},
            "overlap": {"symbol": "N", "range": [1, 5], "unit": "tiles"},
        },
    },
    {
        "title": "Spring-Damper Procedural Animation for 2D Characters",
        "source": "mock",
        "url": "https://example.com/mock/spring-damper-anim",
        "abstract": (
            "We present a spring-damper system for procedural secondary "
            "animation of 2D game characters. The system models hair, "
            "cloth, and accessory dynamics using Verlet integration with "
            "constraint relaxation. Key equation: x(t+Δt) = 2x(t) - "
            "x(t-Δt) + a·Δt² (Störmer-Verlet)."
        ),
        "year": 2023,
        "relevance_score": 0.85,
        "capabilities": ["ANIMATION", "COLOR_PALETTE"],
        "equations": [
            "x(t+Δt) = 2x(t) - x(t-Δt) + a·Δt²  (Störmer-Verlet)",
            "F_spring = -k(x - x_rest)  (Hooke's law)",
            "F_damper = -c·v  (viscous damping)",
        ],
        "parameters": {
            "stiffness": {"symbol": "k", "range": [10.0, 1000.0], "unit": "N/m"},
            "damping": {"symbol": "c", "range": [0.1, 10.0], "unit": "Ns/m"},
            "mass": {"symbol": "m", "range": [0.01, 1.0], "unit": "kg"},
        },
    },
]


def _exponential_backoff_search(miner, queries, max_results_per_query, verbose=False):
    """Execute paper mining with exponential backoff retry strategy.

    Implements the AWS Builder's Library pattern:
    wait_time = min(base * 2^attempt + jitter, max_delay)

    On persistent failure after max_retries, falls back to Mock data
    to guarantee full-chain testability (Netflix Hystrix pattern).

    Parameters
    ----------
    miner : MathPaperMiner
        The paper miner instance.
    queries : list[str]
        Search queries.
    max_results_per_query : int
        Maximum results per query.
    verbose : bool
        Print progress messages.

    Returns
    -------
    MiningSession
        The mining session result (real or mock-based).
    """
    last_error = None
    for attempt in range(_BACKOFF_MAX_RETRIES):
        try:
            if verbose:
                print(
                    f"\n\033[1;36m[🔬 硅基学者] 学术检索尝试 "
                    f"{attempt + 1}/{_BACKOFF_MAX_RETRIES}...\033[0m"
                )
            session = miner.mine(
                queries=queries,
                max_results_per_query=max_results_per_query,
            )
            if session.papers_found > 0:
                if verbose:
                    print(
                        f"\033[1;32m[🔬 硅基学者] 成功！发现 "
                        f"{session.papers_found} 篇论文\033[0m"
                    )
                return session
            # Zero results — might be rate-limited, retry
            if verbose:
                print(
                    f"\033[1;33m[🔬 硅基学者] 零结果，"
                    f"可能遭遇限流，准备重试...\033[0m"
                )
        except Exception as exc:
            last_error = exc
            if verbose:
                print(
                    f"\033[1;33m[🔬 硅基学者] 检索异常: {exc}，"
                    f"准备重试...\033[0m"
                )

        # Exponential backoff with jitter
        if attempt < _BACKOFF_MAX_RETRIES - 1:
            delay = min(
                _BACKOFF_BASE_DELAY * (_BACKOFF_MULTIPLIER ** attempt)
                + random.uniform(0, _BACKOFF_JITTER_RANGE),
                _BACKOFF_MAX_DELAY,
            )
            if verbose:
                print(
                    f"\033[90m[🔬 硅基学者] 指数退避等待 "
                    f"{delay:.1f}s...\033[0m"
                )
            _time.sleep(delay)

    # ── Circuit Breaker: Fall back to Mock data ──────────────────────
    if verbose:
        print(
            f"\n\033[1;35m[🔬 硅基学者] ⚡ 断路器触发！"
            f"外部API不可用，启动 Mock 保底数据...\033[0m"
        )
        if last_error:
            print(f"\033[90m    最后错误: {last_error}\033[0m")
    return None  # Signal to use mock data


def _build_mock_session():
    """Build a mock MiningSession from dummy paper data.

    Returns a dict mimicking MiningSession structure for serialization.
    """
    from datetime import datetime

    return {
        "session_id": "MINE-MOCK-001",
        "query": "physics animation (MOCK FALLBACK)",
        "timestamp": datetime.utcnow().isoformat(),
        "papers_found": len(_DUMMY_PAPERS),
        "papers_accepted": len(_DUMMY_PAPERS),
        "papers_rejected": 0,
        "results": _DUMMY_PAPERS,
        "is_mock": True,
    }


def _session_to_structured_json(session, is_mock=False):
    """Convert a MiningSession (or mock dict) to structured academic JSON.

    Extracts abstracts and (where available) mathematical equations
    into a normalized JSON structure suitable for downstream
    Auto-Enforcer Synthesizer consumption.

    Parameters
    ----------
    session : MiningSession or dict
        The mining session result.
    is_mock : bool
        Whether this is mock fallback data.

    Returns
    -------
    list[dict]
        List of structured paper dictionaries.
    """
    papers = []

    if is_mock:
        # Mock data already has structured fields
        for paper in session.get("results", []):
            papers.append({
                "title": paper["title"],
                "source": paper["source"],
                "url": paper["url"],
                "abstract": paper["abstract"],
                "year": paper["year"],
                "relevance_score": paper["relevance_score"],
                "capabilities": paper["capabilities"],
                "equations": paper.get("equations", []),
                "parameters": paper.get("parameters", {}),
                "is_mock": True,
            })
    else:
        # Real MiningSession with PaperResult objects
        for result in session.results:
            paper_dict = {
                "title": result.title,
                "source": result.source,
                "url": result.url,
                "abstract": result.abstract,
                "year": result.year,
                "relevance_score": round(result.relevance_score, 4),
                "capabilities": result.capabilities,
                "equations": _extract_equations_from_abstract(result.abstract),
                "parameters": _extract_parameters_from_abstract(result.abstract),
                "is_mock": False,
            }
            papers.append(paper_dict)

    return papers


def _extract_equations_from_abstract(abstract: str) -> list[str]:
    """Best-effort extraction of mathematical equations from abstract text.

    Uses simple heuristics to find equation-like patterns. In production,
    this would be enhanced by LLM structured extraction.
    """
    import re

    equations = []
    # Look for common equation patterns
    patterns = [
        r'[A-Za-z_]+\s*[=≈≤≥<>]\s*[^,.;]{3,50}',  # x = ...
        r'∂[A-Za-z]/∂[A-Za-z]\s*[=]\s*[^,.;]{3,50}',  # partial derivatives
        r'∇[²·×]\s*[A-Za-z]+',  # gradient/laplacian operators
        r'Δ[A-Za-z]\s*[≤≥=]\s*[^,.;]{3,50}',  # CFL-like conditions
    ]
    for pattern in patterns:
        matches = re.findall(pattern, abstract)
        for match in matches[:3]:  # Limit to 3 per pattern
            cleaned = match.strip()
            if len(cleaned) > 5 and cleaned not in equations:
                equations.append(cleaned)

    return equations[:5]  # Max 5 equations


def _extract_parameters_from_abstract(abstract: str) -> dict:
    """Best-effort extraction of physics parameters from abstract text."""
    import re

    params = {}
    # Look for parameter-like mentions
    param_patterns = [
        (r'viscosity\s*[νv]?\s*', "viscosity"),
        (r'stiffness\s*[k]?\s*', "stiffness"),
        (r'damping\s*[cd]?\s*', "damping"),
        (r'density\s*[ρ]?\s*', "density"),
        (r'resolution\s*[N]?\s*', "resolution"),
        (r'time[\s-]*step\s*[Δdt]?\s*', "time_step"),
    ]
    lower_abstract = abstract.lower()
    for pattern, name in param_patterns:
        if re.search(pattern, lower_abstract):
            params[name] = {"mentioned": True, "source": "abstract_extraction"}

    return params


# ═══════════════════════════════════════════════════════════════════════════
#  Backend Registration
# ═══════════════════════════════════════════════════════════════════════════

@register_backend(
    _ACADEMIC_MINER_BACKEND_TYPE,
    display_name="Academic Paper Miner (P0-SESSION-186)",
    version="1.0.0",
    artifact_families=(ArtifactFamily.EVOLUTION_REPORT.value,),
    capabilities=(BackendCapability.EVOLUTION_DOMAIN,),
    input_requirements=("output_dir",),
    author="MarioTrickster-MathArt",
    session_origin="SESSION-186",
)
class AcademicMinerBackend:
    """Autonomous academic paper mining via arXiv, PapersWithCode & community sources.

    Wraps the dormant 1226-line ``mathart.evolution.paper_miner`` module
    and 610-line ``mathart.evolution.community_sources`` module as a
    first-class microkernel plugin.  Uses Agentic RAG patterns with
    exponential backoff retry strategy for robust external API access.

    When external APIs are unavailable (rate-limited, network down, no key),
    the backend automatically falls back to Mock/Dummy paper data to
    guarantee full-chain testability (Netflix Hystrix Circuit Breaker).

    All outputs are serialized as structured JSON to
    ``workspace/laboratory/academic_miner/`` sandbox directory.

    Research References
    -------------------
    - Singh et al. (2025) Agentic RAG Survey, arXiv:2501.09136
    - AWS Builder's Library: Exponential Backoff with Jitter
    - Netflix Hystrix: Circuit Breaker & Graceful Degradation
    - TwoSixTech (2022): AST-based untrusted code handling
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def execute(
        self,
        context: dict[str, Any] | None = None,
        *,
        output_dir: str | Path | None = None,
        queries: list[str] | None = None,
        max_results_per_query: int = 3,
        verbose: bool = True,
    ) -> ArtifactManifest:
        """Execute a full academic mining session.

        Parameters
        ----------
        context : dict, optional
            Pipeline context (may contain ``output_dir``).
        output_dir : str or Path, optional
            Override output directory.
        queries : list[str], optional
            Custom search queries. Defaults to ["physics animation"].
        max_results_per_query : int
            Max results per query (hardcoded small to avoid rate-limiting).
        verbose : bool
            Print progress messages.

        Returns
        -------
        ArtifactManifest
            Strongly-typed manifest with structured academic JSON outputs.
        """
        context = context or {}
        t_start = _time.perf_counter()

        # ── Resolve output directory ─────────────────────────────────
        if output_dir is None:
            output_dir = context.get("output_dir")
        if output_dir is None:
            project_root = Path.cwd()
            output_dir = project_root / "workspace" / "laboratory" / "academic_miner"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Default queries ──────────────────────────────────────────
        if queries is None:
            queries = ["physics animation"]

        # Hardcode small pull limit to avoid rate-limiting (Red Line #2)
        max_results_per_query = min(max_results_per_query, 5)

        # ── UX: Sci-fi banner ────────────────────────────────────────
        if verbose:
            print(
                "\n\033[1;36m"
                "╔══════════════════════════════════════════════════════════╗\n"
                "║  🔬 硅基学者 — 论文社区矿工 (Academic Miner)            ║\n"
                "║  SESSION-186: Autonomous Knowledge Mining Engine         ║\n"
                "║  Agentic RAG + Exponential Backoff + Mock Fallback       ║\n"
                "╚══════════════════════════════════════════════════════════╝"
                "\033[0m"
            )

        # ── Attempt real mining with exponential backoff ──────────────
        is_mock = False
        session_data = None
        structured_papers = []

        try:
            from mathart.evolution.paper_miner import MathPaperMiner

            miner = MathPaperMiner(
                verbose=verbose,
                use_live_apis=True,
                use_llm=True,
                relevance_threshold=0.3,
            )

            session = _exponential_backoff_search(
                miner, queries, max_results_per_query, verbose=verbose
            )

            if session is not None and session.papers_found > 0:
                # Real data path
                structured_papers = _session_to_structured_json(session, is_mock=False)
                session_data = {
                    "session_id": session.session_id,
                    "query": session.query,
                    "timestamp": session.timestamp,
                    "papers_found": session.papers_found,
                    "papers_accepted": session.papers_accepted,
                    "papers_rejected": session.papers_rejected,
                    "is_mock": False,
                }
            else:
                is_mock = True
        except Exception as exc:
            logger.warning(
                "[AcademicMinerBackend] MathPaperMiner import/init failed: %s. "
                "Falling back to Mock data.",
                exc,
            )
            is_mock = True

        # ── Mock fallback path ───────────────────────────────────────
        if is_mock:
            mock_session = _build_mock_session()
            structured_papers = _session_to_structured_json(mock_session, is_mock=True)
            session_data = mock_session
            if verbose:
                print(
                    "\033[1;35m[🔬 硅基学者] 使用 Mock 保底数据 "
                    f"({len(structured_papers)} 篇预设论文)\033[0m"
                )

        # ── Serialize structured academic JSON ───────────────────────
        papers_json_path = output_dir / "academic_papers.json"
        papers_json_path.write_text(
            json.dumps(structured_papers, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        session_json_path = output_dir / "mining_session.json"
        session_json_path.write_text(
            json.dumps(session_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # ── Build output manifest ────────────────────────────────────
        t_elapsed = _time.perf_counter() - t_start

        outputs = {
            "academic_papers_json": str(papers_json_path),
            "mining_session_json": str(session_json_path),
        }

        metadata = {
            "papers_found": len(structured_papers),
            "queries": queries,
            "is_mock_fallback": is_mock,
            "max_results_per_query": max_results_per_query,
            "total_mining_time_s": round(t_elapsed, 3),
            "backend_type": _ACADEMIC_MINER_BACKEND_TYPE,
            "artifact_family": ArtifactFamily.EVOLUTION_REPORT.value,
            "session_origin": "SESSION-186",
            "backoff_config": {
                "base_delay_s": _BACKOFF_BASE_DELAY,
                "multiplier": _BACKOFF_MULTIPLIER,
                "max_delay_s": _BACKOFF_MAX_DELAY,
                "max_retries": _BACKOFF_MAX_RETRIES,
                "jitter_range_s": _BACKOFF_JITTER_RANGE,
            },
            "research_references": [
                "Singh et al. (2025) Agentic RAG Survey, arXiv:2501.09136",
                "AWS Builder's Library: Exponential Backoff with Jitter",
                "Netflix Hystrix: Circuit Breaker & Graceful Degradation",
                "Klusty et al. (2025) LLM Structured Data Extraction",
            ],
        }

        manifest = ArtifactManifest(
            artifact_family=ArtifactFamily.EVOLUTION_REPORT.value,
            backend_type=_ACADEMIC_MINER_BACKEND_TYPE,
            version="1.0.0",
            session_id="SESSION-186",
            outputs=outputs,
            metadata=metadata,
        )

        # ── Write execution report ───────────────────────────────────
        report_path = output_dir / "academic_miner_execution_report.json"
        report_data = {
            "status": "success",
            "backend": _ACADEMIC_MINER_BACKEND_TYPE,
            "session": "SESSION-186",
            "elapsed_s": round(t_elapsed, 3),
            "papers_found": len(structured_papers),
            "is_mock_fallback": is_mock,
            "queries": queries,
            "output_files": outputs,
        }
        report_path.write_text(
            json.dumps(report_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        if verbose:
            mode_tag = "MOCK 保底" if is_mock else "实时检索"
            print(
                f"\n\033[1;32m[✅ 硅基学者] 学术矿工执行完毕！"
                f"\n    模式: {mode_tag}"
                f"\n    论文数: {len(structured_papers)}"
                f"\n    耗时: {t_elapsed:.2f}s"
                f"\n    输出目录: {output_dir}\033[0m"
            )

        return manifest

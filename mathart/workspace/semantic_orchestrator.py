"""Semantic VFX Orchestrator — LLM-Driven Plugin Activation via Intent Parsing.

SESSION-187: P0-SESSION-187-SEMANTIC-ORCHESTRATOR-AND-GRAND-UNIFICATION
SESSION-188: P0-SESSION-188-QUADRUPED-AWAKENING-AND-VAT-BRIDGE

This module implements the **Semantic VFX Orchestrator**, the bridge between
the Director Studio's natural-language intent and the BackendRegistry's
microkernel plugins.  It enables the LLM to act as an **Orchestrator**
(DAG-based tool-use pattern) that dynamically activates VFX plugins based
on user descriptions.

SESSION-188 Enhancement: Skeleton Topology Inference
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The orchestrator now infers ``skeleton_topology`` from user intent,
enabling dynamic dispatch to either the biped or quadruped physics engine.
This is implemented via keyword detection in the vibe text, with the
result propagated to downstream backends via the intent spec.

Research Foundations
--------------------
1. **LLM as Orchestrator / Tool-Use (DAG Pattern)**:
   The LLM outputs a structured JSON containing both physics parameters
   AND an ``active_vfx_plugins`` array.  This array is the "tool invocation
   sequence" that the downstream Pipeline Weaver consumes.
   Ref: Xu et al. (2026) "Evolution of Tool Use in LLM Agents", arXiv:2603.22862;
   Daunis (2025) "Declarative Language for LLM-Powered Agent Workflows",
   arXiv:2512.19769; Azure Architecture Center (2026) "AI Agent Orchestration
   Patterns".

2. **Hallucination Filtering (Intersection Guard)**:
   LLMs are prone to hallucinating non-existent plugin names.  This module
   performs a strict **set intersection** between the LLM's suggested plugins
   and ``BackendRegistry.all_backends().keys()``, discarding any hallucinated
   names with a WARNING log.  This is the "幻觉防呆红线".

3. **Semantic Keyword → Plugin Mapping**:
   A curated ``SEMANTIC_VFX_TRIGGER_MAP`` maps natural-language keywords
   (Chinese + English) to registered backend type names.  This serves as
   the heuristic fallback when no LLM is available.

Architecture Discipline
-----------------------
- This module is a **standalone bridge** — it does NOT modify any core
  pipeline, BackendRegistry, or existing Director Intent parser.
- It is injected into the Director Studio flow via dependency injection.
- Output is always a validated list of backend type strings that exist
  in the live BackendRegistry.

Red-Line Enforcement
--------------------
- 🔴 **Anti-Hardcoded Red Line**: ZERO ``if "cppn" in plugins`` spaghetti.
  All plugin resolution is via registry reflection + set intersection.
- 🔴 **Hallucination Guard Red Line**: Strict intersection filtering.
  Any plugin name not in ``BackendRegistry.all_backends().keys()`` is
  discarded with a WARNING.
- 🔴 **Zero-Trunk-Modification Red Line**: This module does NOT import
  or modify ``AssetPipeline``, ``MicrokernelOrchestrator``, or any
  production pipeline code.
- 🔴 **Implicit Switching Red Line (SESSION-188)**: Topology inference
  is additive — ZERO modification to existing biped dispatch logic.
  Quadruped engine is activated ONLY when topology is explicitly inferred.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Semantic Keyword → VFX Plugin Trigger Map
# ═══════════════════════════════════════════════════════════════════════════
# This map is the heuristic fallback when no LLM is available.
# Each keyword (Chinese or English, lowercased) maps to a list of
# backend type names that should be activated when the keyword appears
# in the user's natural-language description.
#
# Design: The map is intentionally broad — it's better to activate a
# plugin that turns out to be unnecessary (graceful no-op) than to miss
# one that the user intended.
SEMANTIC_VFX_TRIGGER_MAP: Dict[str, List[str]] = {
    # CPPN Texture triggers
    "材质": ["cppn_texture_evolution"],
    "纹理": ["cppn_texture_evolution"],
    "赛博": ["cppn_texture_evolution"],
    "赛博朋克": ["cppn_texture_evolution"],
    "有机纹理": ["cppn_texture_evolution"],
    "procedural": ["cppn_texture_evolution"],
    "texture": ["cppn_texture_evolution"],
    "cyber": ["cppn_texture_evolution"],
    "cyberpunk": ["cppn_texture_evolution"],
    "高精度材质": ["cppn_texture_evolution"],
    "cppn": ["cppn_texture_evolution"],
    # Fluid Momentum VFX triggers
    "水花": ["fluid_momentum_controller"],
    "流水": ["fluid_momentum_controller"],
    "液体": ["fluid_momentum_controller"],
    "流体": ["fluid_momentum_controller"],
    "水流": ["fluid_momentum_controller"],
    "挥刀水花": ["fluid_momentum_controller"],
    "splash": ["fluid_momentum_controller"],
    "fluid": ["fluid_momentum_controller"],
    "water": ["fluid_momentum_controller"],
    "wave": ["fluid_momentum_controller"],
    "流水特效": ["fluid_momentum_controller"],
    # High Precision VAT triggers
    "导出": ["high_precision_vat"],
    "引擎导出": ["high_precision_vat"],
    "高精度": ["high_precision_vat", "cppn_texture_evolution"],
    "vat": ["high_precision_vat"],
    "hdr": ["high_precision_vat"],
    "float": ["high_precision_vat"],
    "引擎": ["high_precision_vat"],
    "工业导出": ["high_precision_vat"],
    "engine_export": ["high_precision_vat"],
    # Quadruped Physics triggers (SESSION-188)
    "四足": ["quadruped_physics"],
    "机械狗": ["quadruped_physics"],
    "赛博狗": ["quadruped_physics"],
    "机械犬": ["quadruped_physics"],
    "四足兽": ["quadruped_physics"],
    "狗": ["quadruped_physics"],
    "犬": ["quadruped_physics"],
    "马": ["quadruped_physics"],
    "狼": ["quadruped_physics"],
    "虎": ["quadruped_physics"],
    "quadruped": ["quadruped_physics"],
    "four-legged": ["quadruped_physics"],
    "dog": ["quadruped_physics"],
    "horse": ["quadruped_physics"],
    "wolf": ["quadruped_physics"],
    "beast": ["quadruped_physics"],
    "creature": ["quadruped_physics"],
    "mech dog": ["quadruped_physics"],
    "cyber dog": ["quadruped_physics"],
    # Combined triggers
    "全特效": ["cppn_texture_evolution", "fluid_momentum_controller", "high_precision_vat", "quadruped_physics"],
    "黑科技全开": ["cppn_texture_evolution", "fluid_momentum_controller", "high_precision_vat", "quadruped_physics"],
    "max_vfx": ["cppn_texture_evolution", "fluid_momentum_controller", "high_precision_vat", "quadruped_physics"],
}


# ═══════════════════════════════════════════════════════════════════════════
#  VFX Plugin Capability Descriptors (for LLM System Prompt injection)
# ═══════════════════════════════════════════════════════════════════════════
VFX_PLUGIN_CAPABILITIES: Dict[str, Dict[str, str]] = {
    "cppn_texture_evolution": {
        "display_name": "CPPN Texture Evolution Engine",
        "description": (
            "Procedural texture generation via Compositional Pattern Producing "
            "Networks. Generates resolution-independent organic/cyberpunk textures "
            "using coordinate-based neural networks. Activate when user mentions: "
            "材质, 纹理, 赛博, 有机, procedural texture, cyberpunk material."
        ),
        "artifact_type": "MATERIAL_BUNDLE (albedo/normal textures)",
    },
    "fluid_momentum_controller": {
        "display_name": "Fluid Momentum VFX Controller",
        "description": (
            "Eulerian-Lagrangian fluid dynamics simulation for splash/water/wave "
            "visual effects. Generates fluid field data (velocity, density, alpha "
            "masks) that can be composited onto character animations. Activate when "
            "user mentions: 水花, 流水, 液体, 流体, splash, fluid, wave effects."
        ),
        "artifact_type": "VFX_FLOWMAP (fluid field data)",
    },
    "high_precision_vat": {
        "display_name": "High-Precision Float VAT Pipeline",
        "description": (
            "Industrial-grade Vertex Animation Texture export in Float32 precision. "
            "Produces .npy + .hdr + Hi-Lo PNG triple export for game engine import. "
            "Activate when user mentions: 导出, 引擎导出, 高精度, VAT, HDR, engine export."
        ),
        "artifact_type": "VAT_BUNDLE (Float32 HDR textures)",
    },
    "quadruped_physics": {
        "display_name": "Quadruped Physics Engine (SESSION-188)",
        "description": (
            "Four-legged creature physics simulation with NSM gait solver. "
            "Generates quadruped locomotion data (trot/pace gaits) with diagonal-pair "
            "contact sequences. Feeds real physics data to VAT pipeline. "
            "Activate when user mentions: 四足, 机械狗, 赛博狗, quadruped, dog, horse, "
            "wolf, beast, creature, four-legged."
        ),
        "artifact_type": "QUADRUPED_MOTION (positions + contact sequence)",
    },
}


def build_vfx_system_prompt_injection(
    registered_backends: Dict[str, Any],
) -> str:
    """Build the VFX plugin capability block for LLM system prompt injection.

    This generates a structured text block that describes all available VFX
    plugins to the LLM, enabling it to intelligently select which plugins
    to activate based on user intent.

    Parameters
    ----------
    registered_backends : dict
        The output of ``BackendRegistry.all_backends()``.

    Returns
    -------
    str
        A formatted text block suitable for injection into the LLM system prompt.
    """
    lines = [
        "",
        "=== Available VFX Plugins (Tools/Capabilities) ===",
        "You MUST include an 'active_vfx_plugins' array in your JSON response.",
        "Based on the user's description, select which plugins to activate.",
        "ONLY use plugin names from the list below. Do NOT invent plugin names.",
        "",
    ]
    # Only include plugins that are actually registered
    available_plugins = []
    for plugin_name, info in VFX_PLUGIN_CAPABILITIES.items():
        if plugin_name in registered_backends:
            available_plugins.append((plugin_name, info))

    if not available_plugins:
        lines.append("(No VFX plugins currently registered)")
    else:
        for plugin_name, info in available_plugins:
            lines.append(f"Plugin: {plugin_name}")
            lines.append(f"  Display Name: {info['display_name']}")
            lines.append(f"  Description: {info['description']}")
            lines.append(f"  Artifact Type: {info['artifact_type']}")
            lines.append("")

    lines.append(
        "If the user's description does not clearly require any VFX plugin, "
        "return an empty array: \"active_vfx_plugins\": []"
    )
    lines.append("=== End VFX Plugins ===")
    lines.append("")
    return "\n".join(lines)


def resolve_vfx_plugins_from_vibe(
    vibe_text: str,
    registered_backend_keys: Set[str],
) -> List[str]:
    """Resolve VFX plugins from a natural-language vibe string using heuristic matching.

    This is the **heuristic fallback** path used when no LLM is available.
    It tokenizes the vibe text and matches against ``SEMANTIC_VFX_TRIGGER_MAP``.

    Parameters
    ----------
    vibe_text : str
        The user's natural-language description (raw_vibe).
    registered_backend_keys : set[str]
        The set of all registered backend names from BackendRegistry.

    Returns
    -------
    list[str]
        Validated list of backend type names to activate.
    """
    if not vibe_text:
        return []

    # Tokenize the vibe text
    tokens = re.split(r"[,;，；\s的]+", vibe_text.strip().lower())
    candidate_plugins: Set[str] = set()

    for token in tokens:
        token = token.strip()
        if not token:
            continue

        # Direct match
        if token in SEMANTIC_VFX_TRIGGER_MAP:
            candidate_plugins.update(SEMANTIC_VFX_TRIGGER_MAP[token])
            continue

        # Partial match (substring)
        for key, plugins in SEMANTIC_VFX_TRIGGER_MAP.items():
            if key in token or token in key:
                candidate_plugins.update(plugins)

    # ═══════════════════════════════════════════════════════════════════════
    #  [幻觉防呆红线] Strict Intersection Guard
    # ═══════════════════════════════════════════════════════════════════════
    validated = candidate_plugins & registered_backend_keys
    hallucinated = candidate_plugins - registered_backend_keys
    if hallucinated:
        logger.warning(
            "[SemanticOrchestrator] Hallucination guard filtered out "
            "non-existent plugins: %s (not in registry)",
            sorted(hallucinated),
        )

    result = sorted(validated)
    if result:
        logger.info(
            "[SemanticOrchestrator] Heuristic VFX resolution: vibe='%s' → plugins=%s",
            vibe_text[:80],
            result,
        )
    return result


def validate_llm_vfx_plugins(
    llm_suggested: List[str],
    registered_backend_keys: Set[str],
) -> List[str]:
    """Validate LLM-suggested VFX plugin names against the live BackendRegistry.

    This implements the **幻觉防呆红线**: any plugin name not found in the
    registry is discarded with a WARNING log.

    Parameters
    ----------
    llm_suggested : list[str]
        The ``active_vfx_plugins`` array from the LLM's JSON response.
    registered_backend_keys : set[str]
        The set of all registered backend names from BackendRegistry.

    Returns
    -------
    list[str]
        Filtered list containing only plugins that exist in the registry.
    """
    if not llm_suggested:
        return []

    # Normalize names
    normalized = [name.strip().lower().replace("-", "_") for name in llm_suggested]

    # [幻觉防呆红线] Strict intersection
    validated = [name for name in normalized if name in registered_backend_keys]
    hallucinated = [name for name in normalized if name not in registered_backend_keys]

    if hallucinated:
        logger.warning(
            "[SemanticOrchestrator] LLM hallucination guard: discarded %d "
            "non-existent plugin(s): %s",
            len(hallucinated),
            hallucinated,
        )

    if validated:
        logger.info(
            "[SemanticOrchestrator] LLM VFX plugins validated: %s",
            validated,
        )

    return validated


def resolve_active_vfx_plugins(
    *,
    raw_vibe: str = "",
    llm_suggested: Optional[List[str]] = None,
) -> List[str]:
    """Top-level convenience: resolve active VFX plugins from intent context.

    Tries LLM-suggested plugins first (if available), then falls back to
    heuristic vibe matching.  Always validates against the live registry.

    Parameters
    ----------
    raw_vibe : str
        The user's natural-language description.
    llm_suggested : list[str] or None
        The ``active_vfx_plugins`` from LLM response (may be None).

    Returns
    -------
    list[str]
        Validated list of backend type names to activate.
    """
    # Import here to avoid circular imports
    from mathart.core.backend_registry import get_registry

    registry = get_registry()
    registered_keys = set(registry.all_backends().keys())

    # Path 1: LLM-suggested plugins (preferred)
    if llm_suggested:
        validated = validate_llm_vfx_plugins(llm_suggested, registered_keys)
        if validated:
            return validated
        # If LLM suggestions were all hallucinated, fall through to heuristic

    # Path 2: Heuristic fallback
    return resolve_vfx_plugins_from_vibe(raw_vibe, registered_keys)



class SemanticOrchestrator:
    """Class-based API for VFX plugin resolution as expected by tests.

    SESSION-188 Enhancement: Now also infers ``skeleton_topology`` from
    the vibe text, returning it alongside the VFX plugin list.
    """

    def resolve_vfx_plugins(self, raw_intent: dict, vibe: str, registry) -> list[str]:
        """Resolve VFX plugins from intent + vibe."""
        registered_keys = set(registry.all_backends().keys())
        llm_suggested = raw_intent.get("active_vfx_plugins", None)

        if llm_suggested:
            validated = validate_llm_vfx_plugins(llm_suggested, registered_keys)
            if validated:
                return validated

        return resolve_vfx_plugins_from_vibe(vibe, registered_keys)

    def infer_skeleton_topology(self, vibe: str) -> str:
        """Infer skeleton topology from natural language vibe text.

        Returns 'quadruped' if any quadruped keyword is detected,
        otherwise returns 'biped' (the default).

        Parameters
        ----------
        vibe : str
            The user's natural-language description.

        Returns
        -------
        str
            Either 'biped' or 'quadruped'.
        """
        from mathart.core.quadruped_physics_backend import infer_skeleton_topology
        return infer_skeleton_topology(vibe)

    def resolve_full_intent(
        self,
        raw_intent: dict,
        vibe: str,
        registry,
    ) -> dict:
        """Resolve both VFX plugins and skeleton topology.

        Returns a dict with 'active_vfx_plugins' and 'skeleton_topology'.
        """
        plugins = self.resolve_vfx_plugins(raw_intent, vibe, registry)
        topology = self.infer_skeleton_topology(vibe)
        return {
            "active_vfx_plugins": plugins,
            "skeleton_topology": topology,
        }

__all__ = [
    "SemanticOrchestrator",
    "SEMANTIC_VFX_TRIGGER_MAP",
    "VFX_PLUGIN_CAPABILITIES",
    "build_vfx_system_prompt_injection",
    "resolve_active_vfx_plugins",
    "resolve_vfx_plugins_from_vibe",
    "validate_llm_vfx_plugins",
]

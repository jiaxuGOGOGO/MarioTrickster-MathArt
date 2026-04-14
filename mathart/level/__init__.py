"""WFC-based procedural level generation for MarioTrickster.

This module implements Wave Function Collapse (WFC) to automatically generate
ASCII-format level layouts that are directly compatible with the main project's
Level Studio system. It learns adjacency rules from existing level templates
and produces new, structurally valid level variations.
"""

from .wfc import WFCGenerator, AdjacencyRules
from .templates import CLASSIC_FRAGMENTS, ELEMENT_MAP

__all__ = ["WFCGenerator", "AdjacencyRules", "CLASSIC_FRAGMENTS", "ELEMENT_MAP"]

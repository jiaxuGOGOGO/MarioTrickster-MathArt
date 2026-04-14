"""Level templates and element definitions for MarioTrickster.

This module defines the 18-element ASCII mapping used by the main project's
Level Studio, along with classic level fragments that serve as training data
for the WFC generator.
"""
from __future__ import annotations


# ── ASCII element mapping (matches main project LevelStudio) ──────────────

ELEMENT_MAP: dict[str, str] = {
    "#": "solid_ground",
    "=": "platform",
    "W": "wall",
    "M": "mario_spawn",
    "T": "trickster_spawn",
    "G": "goal_zone",
    "^": "spike_trap",
    "~": "fire_trap",
    "P": "pendulum_trap",
    "B": "bounce_platform",
    "C": "collapse_platform",
    "-": "one_way_platform",
    ">": "moving_platform",
    "E": "bouncing_enemy",
    "e": "simple_enemy",
    "F": "fake_wall",
    "H": "hidden_passage",
    "o": "collectible",
    ".": "air",
}

# Reverse lookup: name → char
NAME_TO_CHAR: dict[str, str] = {v: k for k, v in ELEMENT_MAP.items()}

# Element categories for constraint reasoning
SOLID_CHARS = {"#", "W", "F"}
PLATFORM_CHARS = {"=", "-", "B", "C", ">"}
HAZARD_CHARS = {"^", "~", "P"}
ENEMY_CHARS = {"E", "e"}
SPAWN_CHARS = {"M", "T"}
SPECIAL_CHARS = {"G", "H", "o"}
AIR_CHAR = "."


# ── Classic level fragments (training data for WFC) ──────────────────────

CLASSIC_FRAGMENTS: dict[str, str] = {
    "tutorial_start": (
        "......................\n"
        "......................\n"
        "......................\n"
        "..M...o...o...o...G..\n"
        "..#...#...#...#...#..\n"
        "..#...#...#...#...#..\n"
        "######################"
    ),
    "bounce_abyss": (
        "......................\n"
        "......................\n"
        "..o.......o.......o..\n"
        "..........E..........\n"
        "..B...B...#...B...B..\n"
        "......................\n"
        "######################"
    ),
    "trap_corridor": (
        "WWWWWWWWWWWWWWWWWWWWWW\n"
        "......................\n"
        "......................\n"
        "....^...~...^...~....\n"
        "######################\n"
        "######################\n"
        "######################"
    ),
    "enemy_gauntlet": (
        "......................\n"
        "......................\n"
        "..e.....E.....e....E.\n"
        "..#.....#.....#....#.\n"
        "..#.....#.....#....#.\n"
        "......................\n"
        "######################"
    ),
    "final_sprint": (
        "......................\n"
        "......................\n"
        "..o...o...o...o...G..\n"
        "..=...=...=...=...#..\n"
        "......^...~...^......\n"
        "......................\n"
        "######################"
    ),
    "vertical_climb": (
        "W..............o....W\n"
        "W....=.............W\n"
        "W..........=.......W\n"
        "W....=.............W\n"
        "W..........=.......W\n"
        "W....=.............W\n"
        "######################"
    ),
    "hidden_secrets": (
        "......................\n"
        "......................\n"
        "..........H...........\n"
        "..F...#...#...#...F..\n"
        "..#...#...#...#...#..\n"
        "..#...#...#...#...#..\n"
        "######################"
    ),
    "moving_platforms": (
        "......................\n"
        "......................\n"
        "..o.......o.......o..\n"
        "..>...>...>...>...>..\n"
        "......................\n"
        "......................\n"
        "######################"
    ),
}


def parse_fragment(text: str) -> list[list[str]]:
    """Parse a fragment string into a 2D grid of characters.

    Returns a list of rows, where each row is a list of single characters.
    """
    rows = text.strip().split("\n")
    return [list(row) for row in rows]


def fragment_to_string(grid: list[list[str]]) -> str:
    """Convert a 2D grid back to a printable string."""
    return "\n".join("".join(row) for row in grid)

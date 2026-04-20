# SESSION-094 WFC Lock-Tile Audit Notes

## Repository Sync

| Item | Value |
|---|---|
| Repository | `jiaxuGOGOGO/MarioTrickster-MathArt` |
| Branch | `main` |
| Latest Commit | `c43149b2264ca244144ba7d70eabb6f1a0e0f146` |
| GitHub head short hash | `c43149b` |
| Latest visible message | `chore: backfill SESSION-093 commit hash in SESSION_HANDOFF.md` |

## SESSION_HANDOFF.md Key State

The current handoff states that **SESSION-093 closed CRITICAL-2.1 / 2.1b** for XPBD contact priority and ground bus compliance. It explicitly marks **CRITICAL-2.2 (WFC lock-tile survival)** as the next ready target. The handoff recommends transferring the same priority-separation discipline used in XPBD into WFC, with locked tiles treated as an immutable authority set.

## External Reference Notes: Townscaper / Oskar Stålberg

From the Game Developer article on Townscaper:

1. **User edits are the trigger**. When a new piece is added or removed, WFC re-evaluates the connected structure so surrounding constraints remain valid.
2. **Constraint ripple is outward**. The placed piece changes the neighborhood possibility space; nearby cells must adapt.
3. **Real-time usage separates hard selection from decoration**. First identify valid modules, then decorate.
4. **Industrial caveat observed in Townscaper**: the article quotes Oskar saying Townscaper may sometimes fail silently and ignore impossible fits in some long thin structures.

### Engineering implication for this task

For this repository, the user explicitly requires the opposite failure policy for locked tiles: **no silent compromise is allowed when a propagation wave conflicts with a locked tile**. Therefore the correct interpretation is:

- Preserve the industrial idea of **user-driven constraints first**.
- Reject the silent-failure compromise for locked tiles.
- Enforce that a locked tile is treated as **ground truth with immutable domain** while still radiating constraints outward to neighbors.

## Immediate Audit Targets

- `mathart/level/constraint_wfc.py`
- `tests/test_constraint_wfc.py`
- Any bridge or exporter logic depending on WFC contradiction behavior

## External Reference Notes: Model Synthesis / CSP / CDB

### Model Synthesis / Paul Merrell

Search results for Merrell's TVCG paper and dissertation consistently describe **user-specified constraints as requirements that the generated output must satisfy**, not soft preferences. This supports treating a designer-locked WFC tile as a **hard initial domain**: the cell's domain is singleton from the start and must not be expanded, shrunk, or replaced by downstream propagation.

### Conflict-Directed Backjumping / CSP

The MIT lecture material and related CBJ references emphasize a core failure rule: when propagation causes a **domain wipe-out** or reveals an explicit contradiction, the solver should not silently keep going. Instead it should identify a dead-end and **backtrack/backjump** based on the conflict source.

### Engineering translation for this repository

1. A locked tile is a **read-only variable assignment** in CSP terms.
2. Neighbor propagation must still radiate outward from the locked tile to prune non-locked neighbors.
3. If a non-locked neighbor imposes a requirement that would empty the domain of a locked tile, the correct behavior is **raise a contradiction immediately**, not mutate the locked tile and not silently ignore the conflict.
4. Tests must include a **forced dead-end** where two incompatible locked assignments produce a contradiction, and must assert the locked assignments remain bitwise/value-wise unchanged after the exception.

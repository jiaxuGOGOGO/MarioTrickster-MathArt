# Session Protocol — Efficiency & Anti-Duplication Guide

> **Purpose**: Ensure every new session starts efficiently, avoids repeating past work,
> and makes measurable forward progress.

## Mandatory Startup Checklist

Every new session MUST complete these steps in order:

1. **Read `SESSION_HANDOFF.md`** — Current state, priorities, blockers
2. **Read `DEDUP_REGISTRY.json`** — Already-absorbed references and completed changes
3. **Read `PROJECT_BRAIN.json`** — Machine-readable project state
4. **Check `STAGNATION_LOG.md`** — Known failure patterns to avoid
5. **Identify the NEXT priority** from the TODO list (not redo past work)

## Anti-Duplication Rules

| Rule | Description |
|------|-------------|
| **No re-research** | If a topic is in `DEDUP_REGISTRY.json → absorbed_references`, do NOT search for it again |
| **No re-implementation** | If a change is in `DEDUP_REGISTRY.json → completed_changes`, do NOT redo it |
| **No re-analysis** | If a stagnation pattern is in `known_stagnation_patterns`, apply the documented fix directly |
| **Append, don't overwrite** | New findings go into the registry; never delete existing entries |

## Session Exit Checklist

Before ending any session:

1. **Update `DEDUP_REGISTRY.json`** — Add new references, changes, patterns
2. **Update `SESSION_HANDOFF.md`** — Current state for next session
3. **Update `PROJECT_BRAIN.json`** — Machine-readable state
4. **Update `SESSION_TRACKER.md`** — Log this session's work
5. **Commit and push** — All changes to GitHub

## Quality Gates

Before declaring any feature "done":

- [ ] Run evaluator test on 3 sample images (blank, good sprite, noise)
- [ ] Verify new code doesn't break existing imports
- [ ] Check that evolution produces measurably different scores
- [ ] Verify GIF/spritesheet export works end-to-end

## Known Efficiency Killers (Avoid These)

1. **Researching the same topic twice** — Always check DEDUP first
2. **Rewriting code that already works** — Focus on gaps, not rewrites
3. **Adding features without testing** — Test immediately after implementation
4. **Ignoring stagnation patterns** — They waste entire sessions
5. **Not updating handoff docs** — Next session starts from scratch

## Priority Framework

When choosing what to work on:

1. **Critical bugs** (things that are broken)
2. **Evolution effectiveness** (can the system actually improve?)
3. **Asset quality** (do outputs look good?)
4. **New capabilities** (new types of assets)
5. **Polish** (nice-to-have improvements)

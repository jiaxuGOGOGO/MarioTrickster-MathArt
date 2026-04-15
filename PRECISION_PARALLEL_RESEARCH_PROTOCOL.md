# Precision Parallel Research Protocol

> **Purpose**: Give MarioTrickster-MathArt a default, high-efficiency research workflow for finding **precise, non-duplicate, implementation-relevant** external references before major upgrades.

## When to Trigger This Protocol

Trigger this protocol whenever one of the following is true:

1. The user explicitly asks for **precise**, **parallel**, **global**, or **comprehensive** web research.
2. The next project upgrade is blocked by missing outside knowledge rather than only local coding.
3. The session risks becoming **formal**, **repetitive**, or **surface-level**, with no genuinely new ideas helping the project.
4. A gap remains open across sessions and would benefit from benchmark-quality references, practical implementation examples, or production standards.
5. The system needs to compare multiple independent candidate directions and cannot rely on one article or one repository.

## Pre-Research Memory Check

Before any broad search, read and respect these files:

1. `DEDUP_REGISTRY.json`
2. `SESSION_PROTOCOL.md`
3. `PROJECT_BRAIN.json`
4. `SESSION_HANDOFF.md`
5. The latest `research_notes_session*.md` file relevant to the same subsystem

If a topic, source family, or implementation direction has already been absorbed, do not search it again unless the current task clearly requires deeper evidence than the repository already has.

## Research Framing Block

Before searching, define these fields in one compact block:

| Field | Meaning |
|------|---------|
| **subsystem** | The specific part of the project being improved |
| **decision_needed** | The exact implementation or roadmap choice that research must unlock |
| **already_known** | What the repository and project memory already know |
| **duplicate_forbidden** | What must not be searched again because it is already covered |
| **success_signal** | What a useful source must provide: algorithm, dataset, benchmark, export rule, code path, or production standard |

If these five fields cannot be stated clearly, the search target is still too vague.

## Query Lattice Method

Do not use one oversized query. Split the bottleneck into a **query lattice** with separate source lenses.

| Lens | Goal |
|------|------|
| **theory** | Find papers, mathematical models, and formal methods |
| **implementation** | Find repos, code articles, engine examples, and integration patterns |
| **production practice** | Find technical talks, studio posts, and engineering breakdowns |
| **market / asset reality** | Find usable asset packs and actual delivery standards on marketplaces |
| **evaluation** | Find benchmarks, rubrics, datasets, and quality criteria |

Always include at least one **English query variant** for every important intent.

## Search Execution Rules

Run search with the following discipline:

1. One search intent per query group.
2. Up to three query variants per intent.
3. Use parallel subtasks when there are at least five independent subtopics.
4. Search for **sources** first, not just answer snippets.
5. Narrow the search only after better terminology emerges.

## Source Ranking Rules

Prioritize sources in this order.

| Rank | Source Type | Typical Value |
|------|-------------|---------------|
| 1 | Papers, theses, official documentation | Algorithms, metrics, formal constraints |
| 2 | High-quality GitHub repositories | Integration patterns and realistic code paths |
| 3 | Technical talks, engineering blogs, postmortems | Production trade-offs and hard-earned workflow rules |
| 4 | Asset marketplaces and production-ready packs | Real output standards and packaging expectations |
| 5 | Generic tutorials | Low priority unless uniquely useful |

A source is worth keeping only if it changes an implementation path, benchmark, acceptance rule, or concrete roadmap decision.

## Validation Rules

Never trust snippets alone. For each promising source:

1. Open the source itself.
2. Extract the specific method, metric, file format, or production principle.
3. State why it matters to MarioTrickster-MathArt specifically.
4. Label it as one of: **implementation now**, **benchmark design**, **future roadmap**, or **discard**.

When multiple sources say the same thing, keep the strongest and mark the rest as duplicates.

## Parallel Research Output Schema

When running parallel research, keep the output schema consistent.

| Field | Meaning |
|------|---------|
| `topic` | Subtopic researched |
| `source_type` | Paper, repo, talk, asset pack, dataset, blog, etc. |
| `title` | Source title |
| `url` | Direct URL |
| `key_takeaway` | One-sentence useful insight |
| `novelty_vs_project` | What is new relative to current project memory |
| `integration_value` | Why it helps implementation or evaluation |
| `priority_hint` | High / medium / low |
| `duplicate_risk` | Low / medium / high |

## Research Note Output Contract

For any non-trivial research sweep, produce a note with these sections:

1. **Trigger**
2. **Duplicate avoidance boundary**
3. **Best new sources by topic**
4. **Concrete implications for MarioTrickster-MathArt**
5. **Recommended next implementation step**
6. **Do not re-search yet**

The goal is to help the **next session decide faster**, not just to archive links.

## Integration Back Into Project Memory

After research is complete:

1. Update `DEDUP_REGISTRY.json` with newly absorbed source families and anti-repeat rules.
2. Update `SESSION_HANDOFF.md` with the recommended reading order and new priorities.
3. Update `PROJECT_BRAIN.json` with the new research entrypoint and reprioritized gaps.
4. If the protocol itself improved, update `SESSION_PROTOCOL.md` and this file.

## Stop Conditions

Stop researching and switch back to implementation when one of the following is true:

1. Two or more strong sources support the same practical conclusion.
2. New results are mostly duplicates or weaker restatements.
3. The next code change is already clear enough to implement.
4. The bottleneck is now engineering, not knowledge.

## Project-Specific Anti-Drift Rules

For this repository specifically:

1. Prefer sources that improve **real output quality**, **benchmark rigor**, or **pipeline closure**.
2. De-prioritize sources that only make the project sound advanced without making assets better.
3. Prefer self-contained and controllable methods unless outside tooling unlocks major leverage.
4. Study marketplaces to learn **deliverable standards**, not just visual inspiration.
5. Favor sources that can be turned into code, metrics, or asset requirements within this repository.

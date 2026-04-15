# Session Protocol — Efficiency & Anti-Duplication Guide

> **Purpose**: Ensure every new session starts efficiently, avoids repeating past work,
> and makes measurable forward progress.

## Mandatory Startup Checklist

Every new session MUST complete these steps in order:

1. **Read `SESSION_HANDOFF.md`** — Current state, priorities, blockers
2. **Read `DEDUP_REGISTRY.json`** — Already-absorbed references and completed changes
3. **Read `PROJECT_BRAIN.json`** — Machine-readable project state
4. **Check `STAGNATION_LOG.md`** — Known failure patterns to avoid
5. **Read `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` if the task may need external references**
6. **Identify the NEXT priority** from the TODO list (not redo past work)

## Default Precision Research Trigger

Trigger the precision parallel research protocol by default when one of these conditions is true:

1. The user explicitly asks for **precise**, **parallel**, **global**, or **comprehensive** web research.
2. The next upgrade is blocked by missing outside knowledge rather than local coding.
3. The session risks becoming **surface-level**, **repetitive**, or **formal** without new ideas.
4. A subsystem gap stays open across sessions and needs stronger benchmarks, implementation references, or production standards.

When triggered, follow `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` before starting wide search.

If available in the environment, also load the reusable skill at `/home/ubuntu/skills/mathart-precision-research/SKILL.md` to reuse the same workflow outside this repository.

## Explicit Trigger Vocabulary

The protocol can still trigger automatically, but the following phrases are now treated as **standard project trigger wording** and should be recognized directly in future conversations.

| Type | Meaning | Standard Phrase |
|------|---------|-----------------|
| **standard** | Start the protocol immediately | **启动精准并行研究协议** |
| **gap-focused** | Research the highest-priority gap first | **围绕当前最大差距做去重并行搜索** |
| **feature-focused** | Research a specific feature before implementing it | **针对这个功能做精准并行参考全网信息** |
| **anti-drift** | Force research when the session may become superficial | **如果你判断当前升级可能流于形式，立即启动研究协议** |
| **research-first** | Research before implementation | **先按研究协议补足参考，再开始实现** |
| **dedup-focused** | Emphasize avoiding duplicate sources | **按去重机制并行搜索，不要重复已有资料** |
| **landing-focused** | Keep only implementation-relevant sources | **只收集能直接指导项目落地实现的资料** |

### Soft vs. Hard Trigger Rule

| Strength | Meaning | Standard Phrase |
|------|---------|-----------------|
| **soft** | Prefer protocol-first research, but allow direct implementation if memory already makes the next step obvious | **优先启用精准并行研究协议** |
| **hard** | Do not implement first; protocol-guided research must happen before coding or major design changes | **本轮禁止直接开做，必须先启动精准并行研究协议** |

Treat close paraphrases as valid triggers whenever the user intent is clearly to do precise, parallel, non-duplicate, implementation-relevant research.

The default standard command for future sessions is:

> **启动精准并行研究协议：围绕当前差距做去重并行搜索，只保留能直接指导 MarioTrickster-MathArt 落地升级的高价值资料。**

## External Research Guardrails

Before any broad search:

1. Define the concrete subsystem and implementation decision to unlock.
2. State what is already known from project memory.
3. State what must not be searched again.
4. Define the success signal for useful sources.
5. Prefer a query lattice over one oversized query.
6. Stop searching when the next code change is already clear.

Do not search broadly just to appear thorough. Search only to improve implementation quality, benchmarks, or deliverable standards.

## Anti-Duplication Rules

| Rule | Description |
|------|-------------|
| **No re-research** | If a topic is in `DEDUP_REGISTRY.json → absorbed_references`, do NOT search for it again |
| **No re-implementation** | If a change is in `DEDUP_REGISTRY.json → completed_changes`, do NOT redo it |
| **No re-analysis** | If a stagnation pattern is in `known_stagnation_patterns`, apply the documented fix directly |
| **Append, don't overwrite** | New findings go into the registry; never delete existing entries |
| **Distill, then store** | Keep only sources that change implementation, evaluation, benchmarks, or roadmap decisions |

## Session Exit Checklist

Before ending any session:

1. **Update `DEDUP_REGISTRY.json`** — Add new references, changes, patterns
2. **Update `SESSION_HANDOFF.md`** — Current state for next session
3. **Update `PROJECT_BRAIN.json`** — Machine-readable state
4. **Update `SESSION_TRACKER.md`** — Log this session's work
5. **Commit and push** — All changes to GitHub
6. **If research was triggered, update `PRECISION_PARALLEL_RESEARCH_PROTOCOL.md` when the workflow itself improved**

## Quality Gates

Before declaring any feature "done":

- [ ] Run evaluator test on 3 sample images (blank, good sprite, noise)
- [ ] Verify new code doesn't break existing imports
- [ ] Check that evolution produces measurably different scores
- [ ] Verify GIF/spritesheet export works end-to-end
- [ ] Verify any research sweep produced a reusable note or a clear decision not to retain it

## Known Efficiency Killers (Avoid These)

1. **Researching the same topic twice** — Always check DEDUP first
2. **Rewriting code that already works** — Focus on gaps, not rewrites
3. **Adding features without testing** — Test immediately after implementation
4. **Ignoring stagnation patterns** — They waste entire sessions
5. **Not updating handoff docs** — Next session starts from scratch
6. **Searching without a decision target** — Broad search becomes expensive noise
7. **Keeping inspirational links without implementation value** — They pollute future sessions

## Priority Framework

When choosing what to work on:

1. **Critical bugs** (things that are broken)
2. **Evolution effectiveness** (can the system actually improve?)
3. **Asset quality** (do outputs look good?)
4. **New capabilities** (new types of assets)
5. **Polish** (nice-to-have improvements)


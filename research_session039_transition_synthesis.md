# SESSION-039 Research Synthesis: Inertialized Transition Synthesis & Runtime Motion Matching Query

## Research Protocol

SESSION-039 was triggered by two remaining gaps in the project's animation pipeline:

1. **Gap 4 (P1-INDUSTRIAL-34B):** The motion matching evaluator could retrieve and score clips but could not synthesize seamless runtime transitions between animation states.
2. **New P1:** State transitions (e.g., Run → Jump) used implicit or no blending, risking foot skating and contact tag destruction.

The research protocol followed three north-star industrial references through the Deep Reading Protocol.

## North-Star References

### 1. David Bollo — Inertialization (GDC 2018)

> "Inertialization: High-Performance Animation Transitions in Gears of War"
> The Coalition / Microsoft, GDC 2018

**Core insight:** Traditional crossfade evaluates BOTH source and target animations during the transition window, doubling cost and — critically — destroying foot contact tags because the blended pose is a weighted average of two unrelated contact states. Inertialization gives the TARGET animation **100% rendering weight immediately** at the moment of transition. The source animation's residual momentum is captured as a per-joint offset (position + velocity) and decayed to zero over a short window using a **quintic polynomial** with boundary conditions x(t₁)=0, v(t₁)=0, a(t₁)=0.

**Key equations:**
- Quintic decay: `x(t) = (1 - t/T)^5 * x₀ + (1 - t/T)^4 * t * v₀` (simplified)
- Boundary conditions ensure C2 continuity at the end of the blend window
- Jerk-minimizing trajectory (Flash & Hogan 1985)

### 2. Daniel Holden — Dead Blending (2023)

> "Dead Blending" — theorangeduck.com, February 2023
> Integrated into Unreal Engine 5.3

**Core insight:** Instead of evaluating the source animation forward during the blend, Dead Blending **extrapolates** the source pose using only the recorded velocity at the transition point, with exponential decay. This "dead" extrapolation is then cross-faded with the target. The result is simpler than Bollo's quintic (no need for acceleration boundary conditions) and more robust because it only requires the current pose + velocity at transition time.

**Key equations:**
- Extrapolated source: `x_dead(t) = x₀ + v₀ * (1 - e^(-t/τ)) * τ`
- Blend: `x_out(t) = lerp(x_dead(t), x_target(t), smoothstep(t/T))`
- Half-life decay: `decay = 2^(-dt/halflife)`

### 3. Simon Clavet — Motion Matching Runtime Query (GDC 2016)

> "Motion Matching and The Road to Next-Gen Animation"
> Ubisoft Montreal, GDC 2016

**Core insight:** Never enter a clip at frame 0 blindly. Instead, compute a cost function over the entire clip and pick the frame with the lowest cost:

```
Cost = w_vel * ||vel_current - vel_candidate||² 
     + w_contact * ||contact_current - contact_candidate||²
     + w_phase * ||phase_current - phase_candidate||²
```

Contact weight should be 2× velocity weight to prevent skating. The optimal entry frame ensures the transition starts from a pose that is already close to the current character state, minimizing the offset that inertialization must decay.

## Implementation Architecture

### Two-Stage Transition Pipeline

```
RuntimeMotionQuery.query_best_entry()  →  TransitionSynthesizer.request_transition()
         ↓                                           ↓
  Find optimal entry frame              Inertialize source→target offset
  (minimize velocity+contact cost)      (quintic or dead blending decay)
         ↓                                           ↓
  EntryFrameResult                      Inertialized output frames
  (clip, frame_idx, cost)               (target contacts always authoritative)
```

### Module Map

| Module | File | Lines | Role |
|--------|------|-------|------|
| `TransitionSynthesizer` | `transition_synthesizer.py` | 912 | Inertialized blending engine (Bollo quintic + Holden dead blending) |
| `RuntimeMotionQuery` | `runtime_motion_query.py` | 929 | UMR-native runtime entry-frame search (Clavet GDC 2016) |
| `PhysicsTestBattery` | `evolution_layer3.py` | +139 | Test 11-12: transition quality + entry frame cost |
| `evaluate_physics_fitness` | `physics_genotype.py` | +91 | Run→Jump transition test in fitness loop |
| `PhysicsKnowledgeDistiller` | `evolution_layer3.py` | +3 rules | Transition/query/pipeline knowledge distillation |

### Layer 3 Evolution Integration

The three-layer evolution loop now includes transition quality in its closed feedback cycle:

1. **TRAIN:** Generate candidate physics/locomotion genotypes
2. **TEST:** Run 12-test battery including transition quality (Test 11) and entry frame cost (Test 12)
3. **DIAGNOSE:** New rules for `FAIL_TRANSITION_QUALITY` → `TUNE_DECAY_HALFLIFE` / `SWITCH_BLEND_STRATEGY`, and `FAIL_ENTRY_FRAME_COST` → `TUNE_ENTRY_WEIGHTS`
4. **EVOLVE:** Mutation guided by diagnosis gene modifications
5. **DISTILL:** Rules 11-13 capture transition synthesis, runtime query, and combined pipeline patterns

## References

[1] D. Bollo, "Inertialization: High-Performance Animation Transitions in Gears of War", GDC 2018.
[2] D. Holden, "Dead Blending", theorangeduck.com, Feb 2023.
[3] D. Holden, "Dead Blending Node in Unreal Engine", Aug 2023.
[4] S. Clavet, "Motion Matching and The Road to Next-Gen Animation", GDC 2016.
[5] D. Holden, "Learned Motion Matching", SIGGRAPH 2020.
[6] T. Flash and N. Hogan, "The Coordination of Arm Movements", Journal of Neuroscience 5(7), 1985.

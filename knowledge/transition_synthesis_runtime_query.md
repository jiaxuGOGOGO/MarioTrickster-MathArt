# Knowledge: Inertialized Transition Synthesis & Runtime Motion Matching Query

> Distilled from SESSION-039 research and implementation.

## Hard Constraints

1. **NEVER use linear crossfade for state transitions.** Crossfade evaluates both source and target animations simultaneously, destroying contact tags and causing foot skating. Target animation must receive 100% rendering weight immediately at the moment of transition. (Bollo GDC 2018)

2. **Contact tags always come from the TARGET frame.** During inertialized transitions, the target animation's foot contact state is authoritative. The source animation's residual momentum is decayed as an additive offset that does not affect contact semantics. (Bollo GDC 2018, Holden 2023)

3. **Never enter a clip at frame 0 blindly.** Compute `Cost = w_vel * diff(velocity) + w_contact * diff(foot_contacts) + w_phase * diff(phase)` and pick the lowest-cost frame. Contact weight should be 2× velocity weight. (Clavet GDC 2016)

## Heuristics

4. **Decay window: 4-6 frames (0.1-0.25 seconds).** Shorter windows feel snappy but may pop; longer windows feel sluggish. For 24fps animation, 5 frames (0.21s) is the sweet spot. (Bollo GDC 2018)

5. **Dead Blending is the safer default.** Quintic inertialization is mathematically optimal but requires careful tuning of the blend time. Dead Blending (Holden 2023) only needs the current pose + velocity and is more robust to edge cases. Use quintic only when jerk minimization is critical. (Holden 2023)

6. **Two-stage pipeline: Query then Inertialize.** First find the optimal entry frame (minimizes velocity/contact mismatch), then apply inertialized blending from that frame. This eliminates both pop artifacts (wrong entry frame) and skating artifacts (crossfade contact destruction).

7. **Feature vector dimensionality: 16-D compact runtime schema.** Root velocity (2D), contact flags (2D), phase sin/cos/velocity (3D), foot velocities (2D), joint proxies (4D), trajectory direction (2D), padding (1D). Sufficient for real-time query without the full 59-dim evaluation schema.

## Soft Defaults

8. **Decay half-life: 0.05 seconds.** For Dead Blending, this produces natural-feeling momentum decay. Increase to 0.08 for heavier characters, decrease to 0.03 for snappy platformer feel.

9. **Entry frame cost threshold: 8.0.** If the best entry frame has cost > 8.0, the transition will likely pop. Consider extending the search window or using a different target clip.

10. **Transition quality threshold: 0.4.** Below this, the inertialization is not converging fast enough. Switch strategy (quintic ↔ dead blending) or reduce decay window.

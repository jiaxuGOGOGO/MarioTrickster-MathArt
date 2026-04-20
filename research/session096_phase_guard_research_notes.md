# SESSION-096 Phase Guard Research Notes

## Browser-captured findings

### Repository context
- Remote repository: `jiaxuGOGOGO/MarioTrickster-MathArt`
- Branch observed on repository page: `main`
- Latest visible commit on repository page: `e5182d4`
- Current local clone HEAD: `e5182d43ac060cf96ab66e560ecd27d520b0d48b`

### Harel Statecharts paper
- Source opened: `https://www.state-machine.com/doc/Harel87.pdf`
- Paper title: **Statecharts: A Visual Formalism for Complex Systems** by David Harel.
- Immediate architectural takeaway for this task: statecharts are built around explicit states and explicit transitions; the runtime semantics should be anchored to the declared transition graph rather than inferred by ad hoc fallbacks.
- Implementation implication for this session: the phase-driven controller should treat allowed state edges as a first-class contract, and any undeclared edge should be rejected instead of being silently routed through a generic fallback path.

## Additional source alignment

The Unreal Engine 5 transition-rule documentation states that transitions define the structure of the state machine, while **transition rules** define when a state may move to another state. The documentation is explicit that a transition rule evaluates to a boolean and only a `true` result permits the move. This is directly relevant to the current task because it implies that the existence of a requested target state is insufficient; each edge must be explicitly declared and guarded before any state update or blending side effect is allowed.

A state-based testing lecture source opened during research further reinforces the negative-testing requirement: illegal events must not corrupt the machine, and the implementation should preserve the last known valid state or otherwise fail safely without unintended side effects. For this repository, that maps cleanly to three mandatory assertions for illegal transitions: the request is rejected, `current_state` remains unchanged, and internal phase-progress variables remain numerically unchanged.

| Source | Key takeaway | Implementation consequence |
|---|---|---|
| Harel Statecharts (1987) | States and transitions are declared structure, not inferred behavior. | Allowed edges must be modeled explicitly as a graph contract. |
| Unreal Engine Transition Rules | A transition occurs only when the corresponding rule evaluates to `true`. | Transition requests must pass a guard before state mutation or frame routing. |
| State-based negative testing guidance | Illegal events must not corrupt state or create hidden side effects. | Rejection path must preserve current state and phase variables exactly. |


"""SESSION-039 Integration Test Suite.

Validates all new modules and their integration with the existing architecture.
"""
import sys
sys.path.insert(0, '.')

# 1. Test transition_synthesizer imports
from mathart.animation.transition_synthesizer import (
    TransitionStrategy, InertializationChannel, DeadBlendingChannel,
    TransitionQualityMetrics, TransitionSynthesizer,
    TransitionPipelineNode, create_transition_synthesizer, inertialize_transition,
)
print('OK: transition_synthesizer imports')

# 2. Test runtime_motion_query imports
from mathart.animation.runtime_motion_query import (
    RuntimeFeatureWeights, RuntimeFeatureVector,
    extract_runtime_features, EntryFrameResult,
    RuntimeMotionDatabase, RuntimeMotionQuery,
    PlaybackState, MotionMatchingRuntime,
    create_runtime_database, create_motion_matching_runtime,
)
print('OK: runtime_motion_query imports')

# 3. Test TransitionStrategy enum
assert TransitionStrategy.INERTIALIZATION.value == 'inertialization'
assert TransitionStrategy.DEAD_BLENDING.value == 'dead_blending'
print('OK: TransitionStrategy enum values')

# 4. Test RuntimeFeatureVector
fv = RuntimeFeatureVector(root_vx=1.0, root_vy=0.0, left_contact=1.0)
arr = fv.to_array()
assert arr.shape == (16,), f'Expected (16,), got {arr.shape}'
assert arr[0] == 1.0  # root_vx
assert arr[2] == 1.0  # left_contact
print('OK: RuntimeFeatureVector')

# 5. Test RuntimeFeatureWeights
w = RuntimeFeatureWeights()
assert w.foot_contact == 2.0  # Contact weight should be highest
print('OK: RuntimeFeatureWeights (contact=2.0x)')

# 6. Test RuntimeMotionDatabase basic operations
db = RuntimeMotionDatabase()
assert len(db.get_clip_names()) == 0
print('OK: RuntimeMotionDatabase empty init')

# 7. Test TransitionSynthesizer creation
synth = TransitionSynthesizer(strategy=TransitionStrategy.DEAD_BLENDING)
assert not synth.is_active
print('OK: TransitionSynthesizer creation')

# 8. Test evolution_layer3 new enums
from mathart.evolution.evolution_layer3 import (
    PhysicsTestResult, DiagnosisAction,
)
assert PhysicsTestResult.FAIL_TRANSITION_QUALITY.value == 'fail_transition_quality'
assert PhysicsTestResult.FAIL_ENTRY_FRAME_COST.value == 'fail_entry_frame_cost'
assert DiagnosisAction.TUNE_DECAY_HALFLIFE.value == 'tune_decay_halflife'
assert DiagnosisAction.TUNE_ENTRY_WEIGHTS.value == 'tune_entry_weights'
assert DiagnosisAction.SWITCH_BLEND_STRATEGY.value == 'switch_blend_strategy'
print('OK: evolution_layer3 new enums')

# 9. Test PhysicsTestBattery with new metrics
from mathart.evolution.evolution_layer3 import PhysicsTestBattery
battery = PhysicsTestBattery()
report = battery.run_full_battery({
    'stability': 0.8, 'damping_quality': 0.7, 'imitation_score': 0.6,
    'energy_efficiency': 0.5, 'anatomical_score': 0.6,
    'motion_match_score': 0.5, 'contact_consistency': 0.6,
    'silhouette_quality': 0.5, 'skating_penalty': 0.01,
    'transition_quality': 0.7,  # SESSION-039
    'entry_frame_cost': 3.0,    # SESSION-039
    'overall': 0.65,
})
assert report.details['n_tests_run'] == 12
print(f'OK: PhysicsTestBattery 12 tests, result={report.result.value}')

# 10. Test convenience factories
synth2 = create_transition_synthesizer(strategy='dead_blending')
assert not synth2.is_active
print('OK: create_transition_synthesizer factory')

# 11. Test RuntimeMotionDatabase with legacy clips
from mathart.animation.rl_locomotion import ReferenceMotionLibrary
db2 = RuntimeMotionDatabase()
db2.add_from_reference_library()
db2.normalize()
clip_names = db2.get_clip_names()
assert len(clip_names) >= 4, f'Expected >= 4 clips, got {len(clip_names)}: {clip_names}'
print(f'OK: RuntimeMotionDatabase loaded {len(clip_names)} clips: {clip_names}')

# 12. Test RuntimeMotionQuery with loaded database
query = RuntimeMotionQuery(db2)
# Get a run frame and query best jump entry
run_frames = db2._clip_frames.get('run', [])
if run_frames:
    mid = len(run_frames) // 2
    result = query.query_best_entry(
        current_frame=run_frames[mid],
        target_clip_name='jump',
        prev_frame=run_frames[max(0, mid-1)],
    )
    assert result.clip_name == 'jump'
    assert result.entry_frame_idx >= 0
    assert result.cost < float('inf')
    print(f'OK: RuntimeMotionQuery Run→Jump: entry_frame={result.entry_frame_idx}, cost={result.cost:.3f}')
else:
    print('SKIP: No run frames available')

# 13. Test full MotionMatchingRuntime
runtime = MotionMatchingRuntime(transition_strategy='dead_blending')
runtime.initialize()
state = runtime.get_state()
assert state.clip_name == ''
# Tick with desired state
frame = runtime.tick(desired_state='run', dt=1/24)
state = runtime.get_state()
assert state.state == 'run'
print(f'OK: MotionMatchingRuntime initialized, state={state.state}')

# Force transition
frame2 = runtime.force_transition('jump', dt=1/24)
state2 = runtime.get_state()
assert state2.state == 'jump'
log = runtime.get_transition_log()
assert len(log) >= 1
print(f'OK: MotionMatchingRuntime Run→Jump transition, log entries={len(log)}')

# Quality metrics
metrics = runtime.get_quality_metrics()
assert 'transition_quality' in metrics
assert 'transition_count' in metrics
print(f'OK: MotionMatchingRuntime quality metrics: {list(metrics.keys())}')

# 14. Test inertialize_transition convenience function
from mathart.animation.unified_motion import pose_to_umr, MotionRootTransform, MotionContactState
source = pose_to_umr(
    {'l_hip': -0.3, 'r_hip': 0.3, 'l_knee': -0.2, 'r_knee': -0.1, 'spine': 0.05},
    time=0.0, phase=0.5, source_state='run',
    root_transform=MotionRootTransform(x=0.0, y=0.0, velocity_x=1.5, velocity_y=0.0),
    contact_tags=MotionContactState(left_foot=False, right_foot=True),
)
target = pose_to_umr(
    {'l_hip': -0.1, 'r_hip': 0.1, 'l_knee': -0.4, 'r_knee': -0.4, 'spine': 0.1},
    time=0.0, phase=0.0, source_state='jump',
    root_transform=MotionRootTransform(x=0.0, y=0.0, velocity_x=0.8, velocity_y=1.0),
    contact_tags=MotionContactState(left_foot=False, right_foot=False),
)
result_frames = inertialize_transition([source, source], [target, target], dt=1/24)
assert len(result_frames) == 2
assert result_frames[0].source_state == 'jump'  # Target state preserved
assert result_frames[0].contact_tags.left_foot == False  # Target contacts preserved
print('OK: inertialize_transition convenience function')

# 15. Test PhysicsKnowledgeDistiller with SESSION-039 rules
from mathart.evolution.evolution_layer3 import PhysicsKnowledgeDistiller
distiller = PhysicsKnowledgeDistiller()
rules = distiller.distill_success(
    physics_geno=type('G', (), {'pd_stiffness_scale': 1.0, 'pd_damping_scale': 0.8, 'contact_friction': 0.5})(),
    loco_geno=type('L', (), {'gait_type': 'run', 'step_frequency': 2.0, 'stride_length': 0.8})(),
    fitness={
        'overall': 0.7, 'stability': 0.8, 'imitation_score': 0.6,
        'motion_match_score': 0.5, 'anatomical_score': 0.6,
        'contact_consistency': 0.7, 'silhouette_quality': 0.6,
        'skating_penalty': 0.02,
        'transition_quality': 0.75,  # SESSION-039
        'entry_frame_cost': 2.5,     # SESSION-039
    },
    archetype='hero',
)
transition_rules = [r for r in rules if r['domain'] in ('transition_synthesis', 'runtime_motion_matching', 'transition_pipeline')]
assert len(transition_rules) >= 2, f'Expected >= 2 transition rules, got {len(transition_rules)}'
print(f'OK: PhysicsKnowledgeDistiller generated {len(transition_rules)} SESSION-039 rules out of {len(rules)} total')

print()
print('=' * 60)
print('=== ALL 15 INTEGRATION TESTS PASSED ===')
print('=' * 60)

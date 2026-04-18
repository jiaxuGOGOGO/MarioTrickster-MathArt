"""SESSION-064: Quick functional verification for microkernel architecture."""
from mathart.core import (
    BackendRegistry, BackendMeta, register_backend, get_registry,
    ArtifactFamily, ArtifactManifest, ArtifactValidationError, validate_artifact,
    EvolutionNiche, NicheRegistry, NicheReport, ParetoFront,
    register_niche, get_niche_registry,
)
from mathart.core.microkernel_orchestrator import MicrokernelOrchestrator, MicrokernelCycleReport
from mathart.core.evolution_loop import ThreeLayerEvolutionLoop
from mathart.core.pipeline_bridge import MicrokernelPipelineBridge

print('=== All SESSION-064 imports OK ===')

# Test backend registry
reg = get_registry()
print(f'Backend registry: {len(reg.all_backends())} backends')

# Test niche registry
nreg = get_niche_registry()
print(f'Niche registry: {len(nreg.all_niches())} niches')

# Test artifact creation and validation
m = ArtifactManifest(
    artifact_family=ArtifactFamily.SPRITE_SHEET.value,
    backend_type='test',
    session_id='SESSION-064',
    outputs={'spritesheet': '/test.png'},
    metadata={'frame_count': 8, 'frame_width': 32, 'frame_height': 32},
    quality_metrics={'test': 0.9}
)
validate_artifact(m)
print(f'Artifact validation: PASS (hash={m.schema_hash[:16]}...)')

# Test Pareto front
from mathart.core.niche_registry import NicheReport, ParetoFront
pf = ParetoFront()
r1 = NicheReport(niche_name='test_a', fitness_scores={'a': 0.9, 'b': 0.1}, pass_gate=True)
r2 = NicheReport(niche_name='test_b', fitness_scores={'a': 0.1, 'b': 0.9}, pass_gate=True)
pf.add_niche_report(r1)
pf.add_niche_report(r2)
front = pf.compute_front()
print(f'Pareto front: {len(front)} solutions')

# Test MicrokernelPipelineBridge
bridge = MicrokernelPipelineBridge(
    project_root='/tmp/test_project',
    session_id='SESSION-064'
)
summary = bridge.get_registry_summary()
print(f'Bridge registry summary: {len(summary)} chars')

# Test version
from mathart import __version__
assert __version__ == '0.55.0', f'Version mismatch: {__version__}'
print(f'Version: {__version__}')

print('=== ALL 6 FUNCTIONAL TESTS PASS ===')

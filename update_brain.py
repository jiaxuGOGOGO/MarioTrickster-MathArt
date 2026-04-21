import json
from datetime import datetime

with open('PROJECT_BRAIN.json', 'r') as f:
    brain = json.load(f)

# Update P1-B2-1 status
for task in brain.get('pending_tasks', []):
    if task.get('id') == 'P1-B2-1':
        task['status'] = 'CLOSED'
        task['completed_in'] = 'SESSION-112'
        task['updated_in'] = 'SESSION-112'

# Add session summary
new_session = {
    "session_id": "SESSION-112",
    "date": datetime.now().strftime("%Y-%m-%d"),
    "focus": "P1-B2-1 High-Order Terrain SDF Primitives (Convex Hull, Bézier, Heightmap)",
    "status": "COMPLETE",
    "summary": "Closed P1-B2-1 by adding create_convex_hull_terrain, create_bezier_terrain, and create_heightmap_terrain to terrain_sensor.py. All primitives are fully tensorized (DOD broadcast) with zero Python scalar loops. Heightmap uses SciPy EDT for O(N) baking and bilinear sampling. Added 20 white-box tests guarding Anti-Pseudo-Distance, Anti-Scalar-Loop, and Anti-Gradient-Jitter red lines. Full suite 388+ core tests PASS.",
    "files_touched": [
        "mathart/animation/terrain_sensor.py",
        "mathart/animation/__init__.py",
        "tests/test_terrain_sensor.py",
        "SESSION_HANDOFF.md",
        "PROJECT_BRAIN.json"
    ],
    "validation": "20 PASS / 0 FAIL (pytest tests/test_terrain_sensor.py -k P1B2_1_and_primitives)"
}

if 'session_summaries' in brain:
    brain['session_summaries'].append(new_session)
elif 'recent_sessions' in brain:
    brain['recent_sessions'].insert(0, new_session)

# Update gap inventory counts
if 'gap_inventory' in brain:
    brain['gap_inventory']['by_status']['TODO'] = max(0, brain['gap_inventory']['by_status'].get('TODO', 1) - 1)
    brain['gap_inventory']['by_status']['CLOSED'] = brain['gap_inventory']['by_status'].get('CLOSED', 0) + 1
    brain['gap_inventory']['active_total'] = max(0, brain['gap_inventory'].get('active_total', 1) - 1)
    brain['gap_inventory']['closed_archived'] = brain['gap_inventory'].get('closed_archived', 0) + 1

brain['last_session_id'] = 'SESSION-112'
brain['last_updated'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

with open('PROJECT_BRAIN.json', 'w') as f:
    json.dump(brain, f, indent=2)
print("PROJECT_BRAIN.json updated successfully.")

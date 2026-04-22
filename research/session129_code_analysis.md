# SESSION-129 Code Analysis: Bone Naming Mismatch

## Root Cause: Quadruped Bone Naming Semantic Gap

NSM gait profile limb names: `front_left`, `front_right`, `hind_left`, `hind_right`
Skeleton bone names: `fl_upper`, `fl_lower`, `fl_paw`, `fr_upper`, `fr_lower`, `fr_paw`, `hl_upper`, `hl_lower`, `hl_paw`, `hr_upper`, `hr_lower`, `hr_paw`

In `_nsm_to_3d_pose()` (line 170-189):
- Only biped mapping exists: `l_foot` -> `l_thigh`, `r_foot` -> `r_thigh`
- Quadruped limb names (`front_left`, `hind_right`, etc.) pass through as-is
- These names don't exist in the skeleton, so Spine silently ignores them → 38 frames of zero motion

## Fix Required:
Add quadruped limb-to-bone mapping in `_nsm_to_3d_pose()`:
- `front_left` → `fl_upper`
- `front_right` → `fr_upper`
- `hind_left` → `hl_upper`
- `hind_right` → `hr_upper`

Plus: Add post-export validation in `export_spine_json()` that checks all animation bone names exist in setup bones.

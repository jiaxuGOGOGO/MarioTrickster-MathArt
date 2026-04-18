# Registry E2E Guard Report

- Timestamp: 2026-04-18T09:35:35.783237+00:00
- Backend count: 9
- Passed: 9
- Failed: 0

## Results

| Backend | Status | Artifact Family | Notes |
|---|---|---|---|
| anti_flicker_render | PASS | composite | preview, report, workflow |
| cel_shading | PASS | cel_shading | shader_source |
| dimension_uplift_mesh | PASS | mesh_obj | cel_shader, material, mesh |
| industrial_sprite | PASS | material_bundle | albedo, depth, mask, normal |
| knowledge_distill | PASS | knowledge_rules | rules_file |
| motion_2d | PASS | sprite_sheet | spritesheet |
| physics_vfx | PASS | vfx_flipbook | atlas |
| urp2d_bundle | PASS | engine_plugin | plugin_source, shader_source, vat_manifest |
| wfc_tilemap | PASS | level_tilemap | tilemap_json |

## Alias Map

| Alias | Canonical |
|---|---|
| anti_flicker | anti_flicker_render |
| anti_flicker_render | anti_flicker_render |
| breakwall | anti_flicker_render |
| cel_shading | cel_shading |
| composite | composite |
| dimension_uplift | dimension_uplift_mesh |
| dimension_uplift_bundle | dimension_uplift_mesh |
| dimension_uplift_mesh | dimension_uplift_mesh |
| industrial_renderer | industrial_sprite |
| industrial_sprite | industrial_sprite |
| industrial_sprite_bundle | industrial_sprite |
| knowledge_distill | knowledge_distill |
| legacy | legacy |
| microkernel | microkernel |
| motion_2d | motion_2d |
| physics_vfx | physics_vfx |
| sparse_ctrl | anti_flicker_render |
| unity_urp2d_bundle | urp2d_bundle |
| unity_urp_2d | urp2d_bundle |
| unity_urp_2d_bundle | urp2d_bundle |
| unity_urp_native | urp2d_bundle |
| urp2d_bundle | urp2d_bundle |
| wfc_tilemap | wfc_tilemap |

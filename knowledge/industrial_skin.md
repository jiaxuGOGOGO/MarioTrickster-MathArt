# Industrial Skin Rules

Durable rules for the repository's industrial 2.5D material-delivery stack.

## Cycle 1

- Case count: `5`
- Mean inside analytic coverage: `1.00`
- Mean depth range: `1.00`
- Mean thickness range: `1.00`
- Mean roughness range: `1.00`
- Export success ratio: `1.00`
- Acceptance: `True`

## Distilled Rules

### SKIN-001-A

- Rule: For canonical 2D body primitives, gradients must come from analytic distance-plus-gradient contracts; sampled differences are fallback only for unsupported composites.
- Parameter: `industrial.gradient_policy`
- Constraint: `gradient_source = analytic || hybrid_fallback`

### SKIN-001-B

- Rule: Every industrial sprite frame must ship as a material bundle containing albedo, normal, depth, thickness, roughness, and mask so downstream 2D engines can light it immediately.
- Parameter: `industrial.bundle_channels`
- Constraint: `required = [albedo, normal, depth, thickness, roughness, mask]`

### SKIN-001-C

- Rule: Thickness is derived from interior negative distance, while roughness is derived from inverse curvature magnitude, and both must remain non-flat across accepted benchmark cases.
- Parameter: `industrial.material_proxy`
- Constraint: `depth_range>0 && thickness_range>0 && roughness_range>0`

### SKIN-001-PASS

- Rule: Cycle 1 produced coverage=1.00, export_success_ratio=1.00, depth_range=1.00, thickness_range=1.00, roughness_range=1.00.
- Parameter: `industrial.acceptance`
- Constraint: `state = pass`

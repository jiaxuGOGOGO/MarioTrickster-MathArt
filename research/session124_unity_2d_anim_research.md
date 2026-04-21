# SESSION-124 Research Notes — Unity 2D Native Animation Format

## 1. Unity YAML Serialization Specification

### File Header
Every Unity YAML asset starts with:
```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
```
The `%TAG` directive defines `!u!` as shorthand for `tag:unity3d.com,2011:`.

### Object Headers
Each object definition: `--- !u!{CLASS_ID} &{FILE_ID}`
- **Class ID 74** = `AnimationClip`
- **Class ID 91** = `AnimatorController`
- **Class ID 1102** = `AnimatorState`
- **Class ID 1107** = `AnimatorStateMachine`
- **Class ID 1101** = `AnimatorStateTransition`
- **&{FILE_ID}** = unique object ID within the file (e.g., `&7400000`)

### AnimationClip Structure (.anim)
```yaml
--- !u!74 &7400000
AnimationClip:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: ClipName
  serializedVersion: 6
  m_Legacy: 0
  m_Compressed: 0
  m_UseHighQualityCurve: 1
  m_RotationCurves: []
  m_CompressedRotationCurves: []
  m_EulerCurves: []           # Euler rotation curves (localEulerAnglesRaw)
  m_PositionCurves: []        # Position curves (m_LocalPosition)
  m_ScaleCurves: []           # Scale curves
  m_FloatCurves: []           # Generic float property curves
  m_PPtrCurves: []
  m_SampleRate: 30            # FPS
  m_WrapMode: 0
  m_Bounds:
    m_Center: {x: 0, y: 0, z: 0}
    m_Extent: {x: 0, y: 0, z: 0}
  m_ClipBindingConstant: ...
  m_AnimationClipSettings:
    serializedVersion: 2
    m_AdditiveReferencePoseClip: {fileID: 0}
    m_AdditiveReferencePoseTime: 0
    m_StartTime: 0
    m_StopTime: 1.0
    m_OrientationOffsetY: 0
    m_Level: 0
    m_CycleOffset: 0
    m_HasAdditiveReferencePose: 0
    m_LoopTime: 1
    m_LoopBlend: 0
    m_LoopBlendOrientation: 0
    m_LoopBlendPositionY: 0
    m_LoopBlendPositionXZ: 0
    m_KeepOriginalOrientation: 0
    m_KeepOriginalPositionY: 1
    m_KeepOriginalPositionXZ: 0
    m_HeightFromFeet: 0
    m_Mirror: 0
  m_EditorCurves: []
  m_EulerEditorCurves: []
  m_HasGenericRootTransform: 0
  m_HasMotionFloatCurves: 0
  m_Events: []
```

### Keyframe Structure (Position/Scale curves)
```yaml
m_PositionCurves:
- curve:
    serializedVersion: 2
    m_Curve:
    - serializedVersion: 3
      time: 0
      value: {x: 0, y: 0, z: 0}
      inSlope: {x: 0, y: 0, z: 0}
      outSlope: {x: 0, y: 0, z: 0}
      tangentMode: 0
      weightedMode: 0
      inWeight: {x: 0.33333334, y: 0.33333334, z: 0.33333334}
      outWeight: {x: 0.33333334, y: 0.33333334, z: 0.33333334}
    m_PreInfinity: 2
    m_PostInfinity: 2
    m_RotationOrder: 4
  path: BoneName
```

### Keyframe Structure (Euler rotation curves)
```yaml
m_EulerCurves:
- curve:
    serializedVersion: 2
    m_Curve:
    - serializedVersion: 3
      time: 0
      value: {x: 0, y: 0, z: 45}
      inSlope: {x: 0, y: 0, z: 0}
      outSlope: {x: 0, y: 0, z: 0}
      tangentMode: 0
      weightedMode: 0
      inWeight: {x: 0.33333334, y: 0.33333334, z: 0.33333334}
      outWeight: {x: 0.33333334, y: 0.33333334, z: 0.33333334}
  path: BoneName
```

### Float Curves (for generic properties)
```yaml
m_FloatCurves:
- curve:
    serializedVersion: 2
    m_Curve:
    - serializedVersion: 3
      time: 0
      value: 1
      inSlope: 0
      outSlope: 0
      tangentMode: 0
      weightedMode: 0
      inWeight: 0.33333334
      outWeight: 0.33333334
  attribute: m_SortingOrder
  path: BoneName
  classID: 212
  script: {fileID: 0}
```

## 2. Left-Handed vs Right-Handed Coordinate System Transformation

### Unity Coordinate System
- **Left-handed** coordinate system
- **Y-up**, **Z-forward**, **X-right**
- Rotation follows left-hand rule

### Math/Physics Standard (Right-Handed)
- Most math libraries (NumPy) and physics engines use right-handed
- Typically Y-up, Z-backward (or Z-up, Y-forward)

### Transformation Rules for 2D Bone Animation
For 2D skeletal animation projected from 3D:
- **Position**: Flip Z-axis (negate Z component)
- **Rotation**: For 2D (Z-axis rotation only), negate the rotation angle when converting from right-hand to left-hand
- **Scale**: No change needed for uniform scale

### Tensor Transformation Matrix
Using NumPy broadcasting for batch conversion:
```python
# Position: flip Z
positions_unity = positions_math.copy()
positions_unity[:, :, 2] *= -1  # Negate Z for all bones, all frames

# Rotation (Euler Z): negate for handedness
rotations_unity = -rotations_math  # For 2D Z-rotation
```

## 3. Euler Angle Continuous Unwrapping

### The Problem
When computing rotation angles from bone transforms using `atan2`, angles wrap at ±180°.
This causes discontinuities: e.g., 179° → -179° creates a 358° jump that Unity interpolates as a full reverse spin.

### The Solution: np.unwrap
```python
import numpy as np
# angles_rad shape: (n_frames,) or (n_frames, n_bones)
unwrapped_rad = np.unwrap(angles_rad, axis=0)
unwrapped_deg = np.degrees(unwrapped_rad)
```
`np.unwrap` adds/subtracts 2π to maintain C0 continuity along the time axis.

### Critical Rule
**MUST** apply `np.unwrap` before converting to degrees and before computing tangent slopes.
Adjacent frame angle difference must NEVER exceed 180°.

## 4. Tangent (Slope) Computation for Keyframes

### Mathematical Definition
For keyframe at index `i` with time `t[i]` and value `v[i]`:
- **outSlope[i]** = (v[i+1] - v[i]) / (t[i+1] - t[i])  for i < n-1
- **inSlope[i]** = (v[i] - v[i-1]) / (t[i] - t[i-1])  for i > 0
- Boundary: inSlope[0] = outSlope[0], outSlope[-1] = inSlope[-1]

### Tensor Implementation
```python
dt = np.diff(times)  # (n_frames-1,)
dv = np.diff(values, axis=0)  # (n_frames-1, ...)
slopes = dv / dt[:, np.newaxis]  # broadcast

out_slopes = np.zeros_like(values)
in_slopes = np.zeros_like(values)
out_slopes[:-1] = slopes
out_slopes[-1] = slopes[-1]
in_slopes[1:] = slopes
in_slopes[0] = slopes[0]
```

## 5. High-Throughput String Template Engine

### Why NOT PyYAML
- `yaml.dump()` is extremely slow for large float arrays
- Cannot produce Unity's custom `!u!` tags correctly
- Cannot replicate Unity's exact inline formatting (e.g., `{x: 0, y: 0, z: 0}`)

### Solution: String Template / f-string Buffer
Use `io.StringIO` or list `.append()` + `''.join()`:
```python
import io
buf = io.StringIO()
buf.write("%YAML 1.1\n")
buf.write("%TAG !u! tag:unity3d.com,2011:\n")
buf.write("--- !u!74 &7400000\n")
buf.write("AnimationClip:\n")
# ... direct string formatting for all fields
```

### Performance Target
- Thousands of keyframes across dozens of bones
- Must complete in milliseconds, not seconds
- ThreadPoolExecutor for parallel bone curve generation

## 6. .meta File and GUID Generation

### .meta File Structure
```yaml
fileFormatVersion: 2
guid: <32-hex-chars>
NativeFormatImporter:
  externalObjects: {}
  mainObjectFileID: 7400000
  userData:
  assetBundleName:
  assetBundleVariant:
```

### Deterministic GUID Generation
**MUST** use `hashlib.md5(asset_name.encode()).hexdigest()` for stable GUIDs.
**NEVER** use `uuid.uuid4()` — random GUIDs break Unity references on re-export.

## 7. AnimatorController Structure (.controller)

### Basic Structure
```yaml
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!91 &9100000
AnimatorController:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {fileID: 0}
  m_PrefabInstance: {fileID: 0}
  m_PrefabAsset: {fileID: 0}
  m_Name: ControllerName
  serializedVersion: 5
  m_AnimatorParameters: []
  m_AnimatorLayers:
  - serializedVersion: 5
    m_Name: Base Layer
    m_StateMachine: {fileID: <state_machine_file_id>}
    ...
```

## References
[1]: https://unity.com/blog/engine-platform/understanding-unitys-serialization-language-yaml "Unity Blog — Understanding Unity's serialization language, YAML"
[2]: https://docs.unity3d.com/520/Documentation/Manual/ClassIDReference.html "Unity YAML Class ID Reference"
[3]: https://github.com/iv4xr-project/labrecruits/blob/master/Unity/AIGym/Assets/Animations/Close%20Doors.anim "Real .anim file example"
[4]: https://numpy.org/doc/stable/reference/generated/numpy.unwrap.html "NumPy unwrap documentation"

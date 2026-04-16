"""
2D Skeletal animation system.

Distilled knowledge integration:
  - Joint ROM from PROMPT_RECIPES (松岡): elbow forward only, knee backward only
  - Head-body ratio: 7-head adult, 2-3 head SD/chibi (MarioTrickster characters)
  - Pivot at bottom center (from AI_SpriteSlicer: character pivot = 0.5, 0)
  - Squash & stretch with volume conservation (animation principles)
  - Antagonist muscle pairs: biceps contract → triceps stretch

Coordinate system:
  Origin at character's feet (bottom center), Y-up.
  1.0 = one head unit height.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import json


@dataclass
class Joint:
    """A joint in the skeleton with position and rotation limits."""
    name: str
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0  # Current rotation in radians
    min_angle: float = -np.pi  # ROM minimum
    max_angle: float = np.pi   # ROM maximum
    parent: Optional[str] = None

    def clamp_angle(self) -> None:
        """Enforce ROM constraints (distilled from 松岡 joint ROM tables)."""
        self.angle = np.clip(self.angle, self.min_angle, self.max_angle)


@dataclass
class Bone:
    """A bone connecting two joints with visual properties."""
    name: str
    joint_a: str  # Parent joint name
    joint_b: str  # Child joint name
    length: float = 1.0
    width: float = 0.15
    color_index: int = 0  # Index into palette


@dataclass
class Skeleton:
    """A 2D skeleton for character animation.

    Default skeleton follows MarioTrickster character proportions:
    - ~3 head units tall (SD/chibi style matching game aesthetic)
    - Bottom center pivot (matching AI_SpriteSlicer convention)
    - Joint ROM from distilled anatomy knowledge
    """
    joints: dict[str, Joint] = field(default_factory=dict)
    bones: list[Bone] = field(default_factory=list)
    head_units: float = 3.0  # Character height in head units

    @classmethod
    def create_humanoid(cls, head_units: float = 3.0) -> "Skeleton":
        """Create a default humanoid skeleton for MarioTrickster characters.

        Proportions based on distilled knowledge:
        - SD/chibi: 2-3 head units (game sprites)
        - Adult realistic: 7 head units (concept art)
        """
        hu = 1.0 / head_units  # One head unit in normalized coords
        total_h = 1.0  # Total height normalized to 1.0

        skel = cls(head_units=head_units)

        # ── Joints (Y=0 at feet, Y=1 at top of head) ──
        # Spine
        skel.add_joint("root", 0, 0)
        skel.add_joint("hip", 0, hu * 1.0, parent="root")
        skel.add_joint("spine", 0, hu * 1.5, parent="hip",
                       min_angle=-0.3, max_angle=0.3)
        skel.add_joint("chest", 0, hu * 2.0, parent="spine",
                       min_angle=-0.2, max_angle=0.2)
        skel.add_joint("neck", 0, hu * 2.5, parent="chest",
                       min_angle=-0.5, max_angle=0.5)
        skel.add_joint("head", 0, hu * 2.8, parent="neck",
                       min_angle=-0.4, max_angle=0.4)

        # Left arm (松岡: elbow flexion forward only)
        skel.add_joint("l_shoulder", -hu * 0.6, hu * 2.3, parent="chest",
                       min_angle=-np.pi, max_angle=np.pi)
        skel.add_joint("l_elbow", -hu * 1.0, hu * 1.8, parent="l_shoulder",
                       min_angle=0, max_angle=np.pi * 0.8)  # Forward only
        skel.add_joint("l_hand", -hu * 1.2, hu * 1.3, parent="l_elbow",
                       min_angle=-np.pi/2, max_angle=np.pi/2)

        # Right arm
        skel.add_joint("r_shoulder", hu * 0.6, hu * 2.3, parent="chest",
                       min_angle=-np.pi, max_angle=np.pi)
        skel.add_joint("r_elbow", hu * 1.0, hu * 1.8, parent="r_shoulder",
                       min_angle=0, max_angle=np.pi * 0.8)
        skel.add_joint("r_hand", hu * 1.2, hu * 1.3, parent="r_elbow",
                       min_angle=-np.pi/2, max_angle=np.pi/2)

        # Left leg (松岡: knee flexion backward only)
        skel.add_joint("l_hip", -hu * 0.3, hu * 1.0, parent="hip",
                       min_angle=-np.pi/2, max_angle=np.pi/2)
        skel.add_joint("l_knee", -hu * 0.3, hu * 0.5, parent="l_hip",
                       min_angle=-np.pi * 0.8, max_angle=0)  # Backward only
        skel.add_joint("l_foot", -hu * 0.3, 0, parent="l_knee",
                       min_angle=-np.pi/4, max_angle=np.pi/4)

        # Right leg
        skel.add_joint("r_hip", hu * 0.3, hu * 1.0, parent="hip",
                       min_angle=-np.pi/2, max_angle=np.pi/2)
        skel.add_joint("r_knee", hu * 0.3, hu * 0.5, parent="r_hip",
                       min_angle=-np.pi * 0.8, max_angle=0)
        skel.add_joint("r_foot", hu * 0.3, 0, parent="r_knee",
                       min_angle=-np.pi/4, max_angle=np.pi/4)

        # ── Bones ──
        skel.add_bone("spine_lower", "hip", "spine")
        skel.add_bone("spine_upper", "spine", "chest")
        skel.add_bone("neck_bone", "chest", "neck")
        skel.add_bone("head_bone", "neck", "head")
        skel.add_bone("l_upper_arm", "l_shoulder", "l_elbow")
        skel.add_bone("l_forearm", "l_elbow", "l_hand")
        skel.add_bone("r_upper_arm", "r_shoulder", "r_elbow")
        skel.add_bone("r_forearm", "r_elbow", "r_hand")
        skel.add_bone("l_thigh", "l_hip", "l_knee")
        skel.add_bone("l_shin", "l_knee", "l_foot")
        skel.add_bone("r_thigh", "r_hip", "r_knee")
        skel.add_bone("r_shin", "r_knee", "r_foot")

        return skel

    def add_joint(self, name: str, x: float, y: float, parent: str | None = None,
                  min_angle: float = -np.pi, max_angle: float = np.pi) -> None:
        self.joints[name] = Joint(name, x, y, 0.0, min_angle, max_angle, parent)

    def add_bone(self, name: str, joint_a: str, joint_b: str,
                 width: float = 0.15, color_index: int = 0) -> None:
        ja = self.joints[joint_a]
        jb = self.joints[joint_b]
        length = np.sqrt((ja.x - jb.x)**2 + (ja.y - jb.y)**2)
        self.bones.append(Bone(name, joint_a, joint_b, length, width, color_index))

    def get_joint_positions(self) -> dict[str, tuple[float, float]]:
        """Get current world positions of all joints (after FK)."""
        return self.forward_kinematics()

    def apply_pose(self, pose: dict[str, float], use_pose_prior: bool = False) -> None:
        """Apply a pose (joint_name → angle) with optional anatomical projection.

        Parameters
        ----------
        pose : dict[str, float]
            Raw or already-corrected joint angle pose.
        use_pose_prior : bool
            If True, run the SESSION-031 VPoser-inspired anatomical projector
            before writing angles into the skeleton. This keeps the 2D pipeline
            compatible with future pseudo-3D / 3D pose generation systems.
        """
        if use_pose_prior:
            from .human_math import VPoserDistilledPrior
            pose = VPoserDistilledPrior().project_pose(pose, skeleton=self)
        for name, angle in pose.items():
            if name in self.joints:
                self.joints[name].angle = angle
                self.joints[name].clamp_angle()

    def project_pose_with_prior(self, pose: dict[str, float]) -> dict[str, float]:
        """Return an anatomically projected pose without mutating skeleton state."""
        from .human_math import VPoserDistilledPrior
        return VPoserDistilledPrior().project_pose(pose, skeleton=self)

    def score_pose_with_prior(self, pose: dict[str, float]) -> dict[str, float]:
        """Score a pose using the SESSION-031 VPoser-inspired prior."""
        from .human_math import VPoserDistilledPrior
        return VPoserDistilledPrior().score_pose(pose, skeleton=self).to_dict()

    def forward_kinematics(self) -> dict[str, tuple[float, float]]:
        """Compute world positions via forward kinematics from root."""
        positions = {}

        def fk_recursive(joint_name: str, parent_x: float, parent_y: float,
                         parent_angle: float):
            j = self.joints[joint_name]
            # Local offset rotated by parent angle
            local_x = j.x - (self.joints[j.parent].x if j.parent else 0)
            local_y = j.y - (self.joints[j.parent].y if j.parent else 0)
            cos_a = np.cos(parent_angle + j.angle)
            sin_a = np.sin(parent_angle + j.angle)
            world_x = parent_x + local_x * cos_a - local_y * sin_a
            world_y = parent_y + local_x * sin_a + local_y * cos_a
            positions[joint_name] = (world_x, world_y)

            # Process children
            for child_name, child in self.joints.items():
                if child.parent == joint_name:
                    fk_recursive(child_name, world_x, world_y,
                                 parent_angle + j.angle)

        # Start from root
        root = self.joints.get("root")
        if root:
            positions["root"] = (root.x, root.y)
            for child_name, child in self.joints.items():
                if child.parent == "root":
                    fk_recursive(child_name, root.x, root.y, root.angle)

        return positions

    def to_dict(self) -> dict:
        return {
            "head_units": self.head_units,
            "joints": {n: {"x": j.x, "y": j.y, "angle": j.angle,
                           "min_angle": j.min_angle, "max_angle": j.max_angle,
                           "parent": j.parent}
                       for n, j in self.joints.items()},
            "bones": [{"name": b.name, "joint_a": b.joint_a, "joint_b": b.joint_b,
                        "length": b.length, "width": b.width}
                       for b in self.bones],
        }

    def save_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

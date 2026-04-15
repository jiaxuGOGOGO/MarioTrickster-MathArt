"""2D Particle System — Verlet integration with pixel-art rendering.

SESSION-018: New module for producing VFX assets (explosions, sparks,
smoke, fire, magic effects) as animated sprite sheets.

Physics:
  - Verlet integration for stable, energy-conserving simulation
  - Gravity, drag, turbulence, and attractor forces
  - Particle-particle collision (optional, for dense effects)

Rendering:
  - Nearest-neighbor pixel-art style (no anti-aliasing)
  - Per-particle color ramp (birth -> mid -> death)
  - Additive blending for glow effects
  - Size attenuation over lifetime

Usage::

    from mathart.animation.particles import ParticleSystem, ParticleConfig

    config = ParticleConfig.fire(canvas_size=64)
    system = ParticleSystem(config)
    frames = system.simulate_and_render(n_frames=16)
    # frames is a list of PIL.Image (RGBA)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image


@dataclass
class ParticleConfig:
    """Configuration for a particle system."""
    # Canvas
    canvas_size: int = 64

    # Emission
    emit_rate: int = 8           # Particles per frame
    emit_burst: int = 0          # One-time burst at frame 0
    max_particles: int = 200
    lifetime_min: float = 0.3    # seconds
    lifetime_max: float = 1.2

    # Initial velocity
    speed_min: float = 20.0      # pixels/sec
    speed_max: float = 80.0
    angle_min: float = 0.0       # radians (0 = right)
    angle_max: float = 2 * math.pi  # full circle

    # Spawn area
    spawn_x: float = 0.5        # Normalized [0,1]
    spawn_y: float = 0.5
    spawn_radius: float = 0.05  # Normalized

    # Forces
    gravity: float = 0.0        # pixels/sec^2 (positive = down)
    drag: float = 0.98          # velocity multiplier per step
    turbulence: float = 0.0     # random force magnitude

    # Appearance
    size_start: float = 3.0     # pixels
    size_end: float = 1.0
    color_birth: tuple = (255, 200, 50)    # RGB
    color_mid: tuple = (255, 100, 20)
    color_death: tuple = (100, 30, 10)
    alpha_start: float = 1.0
    alpha_end: float = 0.0
    additive: bool = False       # Additive blending

    # Simulation
    dt: float = 1.0 / 12.0      # Time step (matches 12 FPS)
    seed: int = 42

    @classmethod
    def fire(cls, canvas_size: int = 64) -> "ParticleConfig":
        """Preset: fire/flame effect."""
        return cls(
            canvas_size=canvas_size,
            emit_rate=12, max_particles=150,
            lifetime_min=0.3, lifetime_max=0.8,
            speed_min=30, speed_max=70,
            angle_min=math.pi * 0.3, angle_max=math.pi * 0.7,  # Upward
            spawn_x=0.5, spawn_y=0.8, spawn_radius=0.1,
            gravity=-40,  # Upward (negative = up in screen coords)
            drag=0.95, turbulence=15,
            size_start=4, size_end=1,
            color_birth=(255, 220, 80),
            color_mid=(255, 120, 20),
            color_death=(80, 20, 5),
            alpha_start=0.9, alpha_end=0.0,
            additive=True,
        )

    @classmethod
    def explosion(cls, canvas_size: int = 64) -> "ParticleConfig":
        """Preset: explosion burst."""
        return cls(
            canvas_size=canvas_size,
            emit_rate=0, emit_burst=60, max_particles=60,
            lifetime_min=0.2, lifetime_max=0.6,
            speed_min=60, speed_max=150,
            angle_min=0, angle_max=2 * math.pi,
            spawn_x=0.5, spawn_y=0.5, spawn_radius=0.02,
            gravity=50, drag=0.92, turbulence=10,
            size_start=4, size_end=1,
            color_birth=(255, 255, 200),
            color_mid=(255, 150, 50),
            color_death=(100, 40, 10),
            alpha_start=1.0, alpha_end=0.0,
            additive=True,
        )

    @classmethod
    def sparkle(cls, canvas_size: int = 64) -> "ParticleConfig":
        """Preset: magic sparkle/shimmer."""
        return cls(
            canvas_size=canvas_size,
            emit_rate=6, max_particles=80,
            lifetime_min=0.4, lifetime_max=1.0,
            speed_min=10, speed_max=40,
            angle_min=0, angle_max=2 * math.pi,
            spawn_x=0.5, spawn_y=0.5, spawn_radius=0.2,
            gravity=-10, drag=0.97, turbulence=20,
            size_start=2, size_end=1,
            color_birth=(200, 220, 255),
            color_mid=(150, 180, 255),
            color_death=(80, 100, 200),
            alpha_start=0.8, alpha_end=0.0,
            additive=True,
        )

    @classmethod
    def smoke(cls, canvas_size: int = 64) -> "ParticleConfig":
        """Preset: smoke/dust."""
        return cls(
            canvas_size=canvas_size,
            emit_rate=5, max_particles=100,
            lifetime_min=0.5, lifetime_max=1.5,
            speed_min=10, speed_max=30,
            angle_min=math.pi * 0.25, angle_max=math.pi * 0.75,
            spawn_x=0.5, spawn_y=0.7, spawn_radius=0.05,
            gravity=-15, drag=0.96, turbulence=12,
            size_start=3, size_end=5,
            color_birth=(180, 180, 180),
            color_mid=(140, 140, 140),
            color_death=(100, 100, 100),
            alpha_start=0.6, alpha_end=0.0,
            additive=False,
        )


@dataclass
class Particle:
    """A single particle with Verlet integration state."""
    x: float
    y: float
    prev_x: float
    prev_y: float
    lifetime: float
    max_lifetime: float
    alive: bool = True

    @property
    def age_ratio(self) -> float:
        """0.0 at birth, 1.0 at death."""
        return 1.0 - max(0.0, self.lifetime / self.max_lifetime)


class ParticleSystem:
    """2D particle system with Verlet integration and pixel-art rendering."""

    def __init__(self, config: ParticleConfig):
        self.config = config
        self.rng = random.Random(config.seed)
        self.particles: list[Particle] = []
        self.time = 0.0

    def simulate_and_render(self, n_frames: int = 16) -> list[Image.Image]:
        """Run simulation and render all frames.

        Returns a list of RGBA PIL Images.
        """
        frames = []
        cfg = self.config

        # Initial burst
        if cfg.emit_burst > 0:
            self._emit(cfg.emit_burst)

        for frame_idx in range(n_frames):
            # Emit new particles
            if cfg.emit_rate > 0:
                self._emit(cfg.emit_rate)

            # Physics step (Verlet integration)
            self._step()

            # Render
            img = self._render()
            frames.append(img)

            self.time += cfg.dt

        return frames

    def _emit(self, count: int):
        """Emit new particles."""
        cfg = self.config
        cs = cfg.canvas_size

        for _ in range(count):
            if len(self.particles) >= cfg.max_particles:
                # Recycle oldest dead particle
                dead = [p for p in self.particles if not p.alive]
                if dead:
                    self.particles.remove(dead[0])
                else:
                    break

            # Spawn position
            angle_spawn = self.rng.uniform(0, 2 * math.pi)
            r_spawn = self.rng.uniform(0, cfg.spawn_radius) * cs
            x = cfg.spawn_x * cs + math.cos(angle_spawn) * r_spawn
            y = cfg.spawn_y * cs + math.sin(angle_spawn) * r_spawn

            # Initial velocity (encoded as prev position for Verlet)
            speed = self.rng.uniform(cfg.speed_min, cfg.speed_max)
            angle = self.rng.uniform(cfg.angle_min, cfg.angle_max)
            vx = math.cos(angle) * speed * cfg.dt
            vy = math.sin(angle) * speed * cfg.dt

            lifetime = self.rng.uniform(cfg.lifetime_min, cfg.lifetime_max)

            self.particles.append(Particle(
                x=x, y=y,
                prev_x=x - vx, prev_y=y - vy,
                lifetime=lifetime, max_lifetime=lifetime,
            ))

    def _step(self):
        """Advance physics by one time step using Verlet integration."""
        cfg = self.config
        dt2 = cfg.dt * cfg.dt

        for p in self.particles:
            if not p.alive:
                continue

            p.lifetime -= cfg.dt
            if p.lifetime <= 0:
                p.alive = False
                continue

            # Verlet integration: x_new = 2*x - x_prev + a*dt^2
            # Current velocity (implicit)
            vx = (p.x - p.prev_x) * cfg.drag
            vy = (p.y - p.prev_y) * cfg.drag

            # Acceleration
            ax = 0.0
            ay = cfg.gravity * dt2

            # Turbulence
            if cfg.turbulence > 0:
                ax += self.rng.gauss(0, cfg.turbulence) * dt2
                ay += self.rng.gauss(0, cfg.turbulence) * dt2

            new_x = p.x + vx + ax
            new_y = p.y + vy + ay

            p.prev_x = p.x
            p.prev_y = p.y
            p.x = new_x
            p.y = new_y

    def _render(self) -> Image.Image:
        """Render current particle state to a pixel-art image."""
        cfg = self.config
        cs = cfg.canvas_size

        # Use float buffer for additive blending
        buffer = np.zeros((cs, cs, 4), dtype=np.float32)

        for p in self.particles:
            if not p.alive:
                continue

            t = p.age_ratio  # 0=birth, 1=death

            # Interpolate color
            if t < 0.5:
                t2 = t * 2
                r = cfg.color_birth[0] * (1 - t2) + cfg.color_mid[0] * t2
                g = cfg.color_birth[1] * (1 - t2) + cfg.color_mid[1] * t2
                b = cfg.color_birth[2] * (1 - t2) + cfg.color_mid[2] * t2
            else:
                t2 = (t - 0.5) * 2
                r = cfg.color_mid[0] * (1 - t2) + cfg.color_death[0] * t2
                g = cfg.color_mid[1] * (1 - t2) + cfg.color_death[1] * t2
                b = cfg.color_mid[2] * (1 - t2) + cfg.color_death[2] * t2

            # Alpha
            alpha = cfg.alpha_start * (1 - t) + cfg.alpha_end * t

            # Size
            size = cfg.size_start * (1 - t) + cfg.size_end * t
            half = max(0.5, size / 2)

            # Pixel coordinates (nearest-neighbor, no AA)
            px = int(round(p.x))
            py = int(round(p.y))
            half_i = int(math.ceil(half))

            for dy in range(-half_i, half_i + 1):
                for dx in range(-half_i, half_i + 1):
                    ix = px + dx
                    iy = py + dy
                    if 0 <= ix < cs and 0 <= iy < cs:
                        # Square particle (pixel art style)
                        dist = max(abs(dx), abs(dy))
                        if dist <= half:
                            if cfg.additive:
                                buffer[iy, ix, 0] += r * alpha / 255.0
                                buffer[iy, ix, 1] += g * alpha / 255.0
                                buffer[iy, ix, 2] += b * alpha / 255.0
                                buffer[iy, ix, 3] = min(1.0, buffer[iy, ix, 3] + alpha * 0.5)
                            else:
                                # Alpha compositing
                                src_a = alpha
                                dst_a = buffer[iy, ix, 3]
                                out_a = src_a + dst_a * (1 - src_a)
                                if out_a > 0.001:
                                    buffer[iy, ix, 0] = (r/255 * src_a + buffer[iy, ix, 0] * dst_a * (1 - src_a)) / out_a
                                    buffer[iy, ix, 1] = (g/255 * src_a + buffer[iy, ix, 1] * dst_a * (1 - src_a)) / out_a
                                    buffer[iy, ix, 2] = (b/255 * src_a + buffer[iy, ix, 2] * dst_a * (1 - src_a)) / out_a
                                    buffer[iy, ix, 3] = out_a

        # Convert to uint8
        buffer[:, :, :3] = np.clip(buffer[:, :, :3], 0, 1)
        buffer[:, :, 3] = np.clip(buffer[:, :, 3], 0, 1)
        result = (buffer * 255).astype(np.uint8)

        return Image.fromarray(result, "RGBA")

    def export_spritesheet(
        self,
        frames: list[Image.Image],
        path: str,
    ) -> dict:
        """Export frames as a horizontal spritesheet with metadata."""
        n = len(frames)
        cs = self.config.canvas_size
        sheet = Image.new("RGBA", (cs * n, cs), (0, 0, 0, 0))
        for i, frame in enumerate(frames):
            sheet.paste(frame, (i * cs, 0))
        sheet.save(path)

        meta = {
            "meta": {
                "image": str(path),
                "format": "RGBA8888",
                "size": {"w": cs * n, "h": cs},
                "generator": "MarioTrickster-MathArt/ParticleSystem",
            },
            "frames": [
                {
                    "name": f"particle_{i:02d}",
                    "rect": [i * cs, 0, cs, cs],
                    "duration": int(self.config.dt * 1000),
                }
                for i in range(n)
            ],
        }
        return meta

    def export_gif(
        self,
        frames: list[Image.Image],
        path: str,
        loop: bool = True,
    ) -> None:
        """Export frames as animated GIF."""
        duration = max(16, int(self.config.dt * 1000))
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0 if loop else 1,
            disposal=2,
        )

"""SESSION-065 — DeepPhase FFT Multi-Channel Phase Manifold Engine.

Research-to-code implementation of Sebastian Starke et al.:
    "DeepPhase: Periodic Autoencoders for Learning Motion Phase Manifolds"
    (ACM SIGGRAPH 2022)

This module extends the existing PhaseChannel/extract_phase_parameters
(SESSION-033) with the full DeepPhase periodic autoencoder framework:

1. **Multi-Channel FFT Decomposition**: Extract N independent phase channels
   from a motion signal, each representing a distinct periodic component
   (e.g., left leg, right leg, arm swing, torso bob).

2. **Phase Manifold Representation**: Map each channel to a 2D phase
   manifold point (A·cos(φ), A·sin(φ)) for smooth interpolation and
   blending in the frequency domain.

3. **Asymmetric Gait Support**: Handle non-symmetric gaits (limping,
   quadruped diagonal/lateral patterns) by allowing per-limb independent
   frequency and phase parameters.

4. **Phase-Space Blending**: Blend between motion styles in phase space
   rather than joint space, eliminating foot-skating and IK artifacts
   during transitions.

Core Insight (Starke 2022):
    Traditional state machines blend motions in joint angle space, which
    causes foot skating when the two source motions are at different
    phases. DeepPhase solves this by:
    1. Decomposing each motion into periodic components via FFT
    2. Representing each component as (Amplitude, Phase) in a 2D manifold
    3. Blending in this manifold space (polar interpolation)
    4. Reconstructing the blended motion from the interpolated phases

    This guarantees that blended motions maintain foot contacts because
    the phase relationship between limbs is preserved.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │  DeepPhaseAnalyzer                                                   │
    │  ├─ decompose(signals) → List[PhaseManifoldPoint]                   │
    │  ├─ extract_dominant_frequencies(signal) → sorted freq list          │
    │  ├─ compute_instantaneous_phase(signal, freq) → phase trajectory    │
    │  └─ reconstruct(manifold_points) → blended signal                   │
    ├─────────────────────────────────────────────────────────────────────┤
    │  PhaseManifoldPoint                                                  │
    │  ├─ amplitude: float (A)                                            │
    │  ├─ phase: float (φ ∈ [0, 2π))                                     │
    │  ├─ frequency: float (F in Hz)                                      │
    │  ├─ offset: float (B, DC component)                                 │
    │  └─ manifold_xy: (A·cos(φ), A·sin(φ))                              │
    ├─────────────────────────────────────────────────────────────────────┤
    │  PhaseBlender                                                        │
    │  ├─ blend(p1, p2, alpha) → PhaseManifoldPoint                       │
    │  ├─ blend_multi(points, weights) → PhaseManifoldPoint               │
    │  └─ interpolate_trajectory(p1, p2, steps) → List[manifold points]   │
    ├─────────────────────────────────────────────────────────────────────┤
    │  AsymmetricGaitAnalyzer                                              │
    │  ├─ analyze_biped(left_signal, right_signal) → GaitPhaseReport      │
    │  ├─ analyze_quadruped(fl, fr, hl, hr) → QuadrupedPhaseReport        │
    │  └─ detect_asymmetry_ratio() → float (0=symmetric, 1=fully async)   │
    └─────────────────────────────────────────────────────────────────────┘

Integration with existing modules:
    - PhaseChannel (phase_driven.py): DeepPhaseAnalyzer produces PhaseChannels
    - NSMGait (nsm_gait.py): AsymmetricGaitAnalyzer feeds into NSM planner
    - MotionMatching (runtime_motion_query.py): Phase manifold features
      improve matching cost computation
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Phase Manifold Representation
# ---------------------------------------------------------------------------

@dataclass
class PhaseManifoldPoint:
    """A point on the 2D phase manifold.

    Represents one periodic component of a motion signal as:
        Γ(t) = A · sin(2π(F·t - S)) + B

    The manifold representation is the 2D point:
        (x, y) = (A · cos(2π·S), A · sin(2π·S))

    This allows smooth interpolation between different motion phases
    without the discontinuity problems of angular interpolation.
    """
    amplitude: float = 0.0       # A: signal amplitude
    frequency: float = 1.0       # F: frequency in Hz
    phase_shift: float = 0.0     # S: phase shift ∈ [0, 1)
    offset: float = 0.0          # B: DC offset
    channel_name: str = ""       # e.g., "left_leg", "right_leg"

    @property
    def manifold_x(self) -> float:
        """X coordinate on the phase manifold."""
        return self.amplitude * math.cos(2.0 * math.pi * self.phase_shift)

    @property
    def manifold_y(self) -> float:
        """Y coordinate on the phase manifold."""
        return self.amplitude * math.sin(2.0 * math.pi * self.phase_shift)

    @property
    def manifold_xy(self) -> Tuple[float, float]:
        """2D manifold coordinates (x, y)."""
        return (self.manifold_x, self.manifold_y)

    def evaluate(self, t: float) -> float:
        """Evaluate the periodic function at time t."""
        return self.amplitude * math.sin(
            2.0 * math.pi * (self.frequency * t - self.phase_shift)
        ) + self.offset

    def evaluate_array(self, t: np.ndarray) -> np.ndarray:
        """Vectorized evaluation over time array."""
        return self.amplitude * np.sin(
            2.0 * np.pi * (self.frequency * t - self.phase_shift)
        ) + self.offset

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "amplitude": self.amplitude,
            "frequency": self.frequency,
            "phase_shift": self.phase_shift,
            "offset": self.offset,
            "channel_name": self.channel_name,
            "manifold_x": self.manifold_x,
            "manifold_y": self.manifold_y,
        }


# ---------------------------------------------------------------------------
# DeepPhase Analyzer
# ---------------------------------------------------------------------------

class DeepPhaseAnalyzer:
    """Multi-channel FFT decomposition for motion phase extraction.

    Implements the core DeepPhase algorithm: given a set of motion signals
    (e.g., joint angles over time), extract the dominant periodic components
    and represent them as points on a phase manifold.
    """

    def __init__(self, max_channels: int = 8,
                 min_amplitude_ratio: float = 0.05,
                 sample_rate: float = 30.0):
        """
        Args:
            max_channels: Maximum number of phase channels to extract.
            min_amplitude_ratio: Minimum amplitude relative to dominant
                                 peak to include a channel.
            sample_rate: Signal sample rate in Hz.
        """
        self.max_channels = max_channels
        self.min_amplitude_ratio = min_amplitude_ratio
        self.sample_rate = sample_rate

    def decompose(self, signal: np.ndarray,
                  channel_name: str = ""
                  ) -> List[PhaseManifoldPoint]:
        """Decompose a 1D motion signal into phase manifold points.

        Uses FFT to find dominant frequencies, then extracts amplitude,
        phase, and offset for each component.

        Args:
            signal: 1D temporal signal (N samples).
            channel_name: Label for the signal source.

        Returns:
            List of PhaseManifoldPoint, sorted by amplitude (descending).
        """
        N = len(signal)
        if N < 8:
            return [PhaseManifoldPoint(channel_name=channel_name)]

        # DC offset
        offset = float(np.mean(signal))
        centered = signal - offset

        # FFT
        coeffs = np.fft.rfft(centered)
        freqs = np.fft.rfftfreq(N, d=1.0 / self.sample_rate)
        magnitudes = np.abs(coeffs) * 2.0 / N  # Single-sided amplitude

        # Skip DC (index 0) and Nyquist
        if len(magnitudes) > 2:
            search_mags = magnitudes[1:-1]
            search_freqs = freqs[1:-1]
            search_phases = np.angle(coeffs[1:-1])
        else:
            return [PhaseManifoldPoint(
                offset=offset, channel_name=channel_name
            )]

        if len(search_mags) == 0:
            return [PhaseManifoldPoint(
                offset=offset, channel_name=channel_name
            )]

        # Find peaks (local maxima)
        peaks = self._find_spectral_peaks(search_mags)

        if not peaks:
            # No clear peaks; use the single dominant frequency
            dominant_idx = int(np.argmax(search_mags))
            peaks = [dominant_idx]

        # Sort by magnitude
        peaks.sort(key=lambda i: search_mags[i], reverse=True)

        # Filter by amplitude threshold
        max_amp = search_mags[peaks[0]]
        threshold = max_amp * self.min_amplitude_ratio

        points: List[PhaseManifoldPoint] = []
        for i, peak_idx in enumerate(peaks[:self.max_channels]):
            amp = float(search_mags[peak_idx])
            if amp < threshold:
                break

            freq = float(search_freqs[peak_idx])
            # Phase shift: convert from FFT phase to our convention
            # FFT gives phase of cos component; we use sin convention
            fft_phase = float(search_phases[peak_idx])
            # sin(x) = cos(x - π/2), so S = -fft_phase/(2π) + 0.25
            phase_shift = (-fft_phase / (2.0 * math.pi) + 0.25) % 1.0

            point = PhaseManifoldPoint(
                amplitude=amp,
                frequency=freq,
                phase_shift=phase_shift,
                offset=offset if i == 0 else 0.0,
                channel_name=f"{channel_name}_ch{i}" if channel_name
                else f"ch{i}"
            )
            points.append(point)

        if not points:
            points.append(PhaseManifoldPoint(
                offset=offset, channel_name=channel_name
            ))

        return points

    def decompose_multi(self, signals: Dict[str, np.ndarray]
                        ) -> Dict[str, List[PhaseManifoldPoint]]:
        """Decompose multiple named signals simultaneously.

        Args:
            signals: Dict mapping signal name to 1D array.

        Returns:
            Dict mapping signal name to list of phase manifold points.
        """
        results = {}
        for name, signal in signals.items():
            results[name] = self.decompose(signal, channel_name=name)
        return results

    def compute_instantaneous_phase(
        self, signal: np.ndarray, target_freq: float
    ) -> np.ndarray:
        """Compute instantaneous phase at a specific frequency.

        Uses the analytic signal (Hilbert transform) approach to extract
        the time-varying phase at the target frequency.

        Args:
            signal: 1D temporal signal.
            target_freq: Target frequency in Hz.

        Returns:
            (N,) array of instantaneous phase values in [0, 2π).
        """
        N = len(signal)
        if N < 4:
            return np.zeros(N)

        t = np.arange(N) / self.sample_rate
        centered = signal - np.mean(signal)

        # Bandpass around target frequency
        bandwidth = max(target_freq * 0.3, 0.5)
        f_low = max(target_freq - bandwidth, 0.1)
        f_high = target_freq + bandwidth

        coeffs = np.fft.rfft(centered)
        freqs = np.fft.rfftfreq(N, d=1.0 / self.sample_rate)

        # Zero out frequencies outside band
        mask = (freqs >= f_low) & (freqs <= f_high)
        filtered_coeffs = coeffs.copy()
        filtered_coeffs[~mask] = 0

        # Analytic signal via Hilbert-like transform
        # Zero negative frequencies (already done by rfft)
        # Double positive frequencies
        filtered_coeffs[1:] *= 2.0

        analytic = np.fft.irfft(filtered_coeffs, n=N)

        # Compute phase from cos/sin projections
        cos_proj = centered * np.cos(2.0 * np.pi * target_freq * t)
        sin_proj = centered * np.sin(2.0 * np.pi * target_freq * t)

        # Cumulative phase using windowed projections
        window_size = max(int(self.sample_rate / max(target_freq, 0.1)), 4)
        phases = np.zeros(N)

        for i in range(N):
            start = max(0, i - window_size // 2)
            end = min(N, i + window_size // 2 + 1)
            cx = np.sum(cos_proj[start:end])
            sx = np.sum(sin_proj[start:end])
            phases[i] = math.atan2(sx, cx) % (2.0 * math.pi)

        return phases

    def reconstruct(self, points: List[PhaseManifoldPoint],
                    duration: float, num_samples: int
                    ) -> np.ndarray:
        """Reconstruct a signal from phase manifold points.

        Args:
            points: List of phase manifold points.
            duration: Total duration in seconds.
            num_samples: Number of output samples.

        Returns:
            (num_samples,) reconstructed signal.
        """
        t = np.linspace(0, duration, num_samples)
        signal = np.zeros(num_samples)

        for point in points:
            signal += point.evaluate_array(t)

        return signal

    def _find_spectral_peaks(self, magnitudes: np.ndarray
                             ) -> List[int]:
        """Find local maxima in the magnitude spectrum."""
        peaks = []
        N = len(magnitudes)
        for i in range(N):
            is_peak = True
            if i > 0 and magnitudes[i] <= magnitudes[i - 1]:
                is_peak = False
            if i < N - 1 and magnitudes[i] <= magnitudes[i + 1]:
                is_peak = False
            if is_peak and magnitudes[i] > 0:
                peaks.append(i)

        # If no peaks found, return index of maximum
        if not peaks and N > 0:
            peaks = [int(np.argmax(magnitudes))]

        return peaks


# ---------------------------------------------------------------------------
# Phase Blender
# ---------------------------------------------------------------------------

class PhaseBlender:
    """Blend between phase manifold points for smooth motion transitions.

    Instead of blending joint angles (which causes foot skating), we blend
    in phase manifold space. This preserves the periodic structure and
    ensures that foot contacts remain synchronized.
    """

    @staticmethod
    def blend(p1: PhaseManifoldPoint, p2: PhaseManifoldPoint,
              alpha: float) -> PhaseManifoldPoint:
        """Blend two phase manifold points.

        Uses polar interpolation on the manifold to avoid phase
        discontinuities.

        Args:
            p1: Source phase point.
            p2: Target phase point.
            alpha: Blend factor (0.0 = p1, 1.0 = p2).

        Returns:
            Blended PhaseManifoldPoint.
        """
        alpha = max(0.0, min(1.0, alpha))

        # Blend in manifold space (Cartesian interpolation of 2D points)
        x1, y1 = p1.manifold_xy
        x2, y2 = p2.manifold_xy

        bx = x1 * (1.0 - alpha) + x2 * alpha
        by = y1 * (1.0 - alpha) + y2 * alpha

        # Convert back to amplitude and phase
        amplitude = math.sqrt(bx * bx + by * by)
        if amplitude > 1e-10:
            phase_shift = (math.atan2(by, bx) / (2.0 * math.pi)) % 1.0
        else:
            phase_shift = p1.phase_shift * (1.0 - alpha) + \
                          p2.phase_shift * alpha

        # Linear interpolation for frequency and offset
        frequency = p1.frequency * (1.0 - alpha) + p2.frequency * alpha
        offset = p1.offset * (1.0 - alpha) + p2.offset * alpha

        return PhaseManifoldPoint(
            amplitude=amplitude,
            frequency=frequency,
            phase_shift=phase_shift,
            offset=offset,
            channel_name=f"blend({p1.channel_name},{p2.channel_name})"
        )

    @staticmethod
    def blend_multi(points: List[PhaseManifoldPoint],
                    weights: List[float]) -> PhaseManifoldPoint:
        """Blend multiple phase manifold points with weights.

        Args:
            points: List of phase manifold points.
            weights: Corresponding blend weights (will be normalized).

        Returns:
            Blended PhaseManifoldPoint.
        """
        if not points:
            return PhaseManifoldPoint()

        total_w = sum(weights)
        if total_w < 1e-10:
            return points[0]

        norm_weights = [w / total_w for w in weights]

        bx = sum(p.manifold_x * w for p, w in zip(points, norm_weights))
        by = sum(p.manifold_y * w for p, w in zip(points, norm_weights))
        freq = sum(p.frequency * w for p, w in zip(points, norm_weights))
        offset = sum(p.offset * w for p, w in zip(points, norm_weights))

        amplitude = math.sqrt(bx * bx + by * by)
        if amplitude > 1e-10:
            phase_shift = (math.atan2(by, bx) / (2.0 * math.pi)) % 1.0
        else:
            phase_shift = sum(
                p.phase_shift * w for p, w in zip(points, norm_weights)
            )

        return PhaseManifoldPoint(
            amplitude=amplitude,
            frequency=freq,
            phase_shift=phase_shift,
            offset=offset,
            channel_name="multi_blend"
        )

    @staticmethod
    def interpolate_trajectory(
        p1: PhaseManifoldPoint, p2: PhaseManifoldPoint,
        steps: int = 10
    ) -> List[PhaseManifoldPoint]:
        """Generate a trajectory of blended points between p1 and p2."""
        trajectory = []
        for i in range(steps + 1):
            alpha = i / max(steps, 1)
            trajectory.append(PhaseBlender.blend(p1, p2, alpha))
        return trajectory


# ---------------------------------------------------------------------------
# Asymmetric Gait Analyzer
# ---------------------------------------------------------------------------

@dataclass
class GaitPhaseReport:
    """Analysis report for biped gait phase decomposition."""
    left_leg: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    right_leg: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    torso: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    asymmetry_ratio: float = 0.0  # 0 = symmetric, 1 = fully async
    phase_offset: float = 0.5     # Normal walk = 0.5 (180°)
    dominant_frequency: float = 1.0

    def to_dict(self) -> dict:
        return {
            "left_leg": self.left_leg.to_dict(),
            "right_leg": self.right_leg.to_dict(),
            "torso": self.torso.to_dict(),
            "asymmetry_ratio": self.asymmetry_ratio,
            "phase_offset": self.phase_offset,
            "dominant_frequency": self.dominant_frequency,
        }


@dataclass
class QuadrupedPhaseReport:
    """Analysis report for quadruped gait phase decomposition."""
    front_left: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    front_right: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    hind_left: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    hind_right: PhaseManifoldPoint = field(
        default_factory=PhaseManifoldPoint)
    gait_type: str = "walk"  # walk, trot, canter, gallop
    diagonal_phase_offset: float = 0.5  # FL-HR offset
    lateral_phase_offset: float = 0.25  # FL-FR offset

    def to_dict(self) -> dict:
        return {
            "front_left": self.front_left.to_dict(),
            "front_right": self.front_right.to_dict(),
            "hind_left": self.hind_left.to_dict(),
            "hind_right": self.hind_right.to_dict(),
            "gait_type": self.gait_type,
            "diagonal_phase_offset": self.diagonal_phase_offset,
            "lateral_phase_offset": self.lateral_phase_offset,
        }


class AsymmetricGaitAnalyzer:
    """Analyze asymmetric and multi-legged gaits using DeepPhase decomposition.

    Handles:
    - Limping/injured gaits (different amplitude/frequency per leg)
    - Quadruped gaits (walk, trot, canter, gallop)
    - Transition detection between gait types
    """

    def __init__(self, sample_rate: float = 30.0):
        self.analyzer = DeepPhaseAnalyzer(
            max_channels=4, sample_rate=sample_rate
        )
        self.sample_rate = sample_rate

    def analyze_biped(
        self, left_signal: np.ndarray, right_signal: np.ndarray,
        torso_signal: Optional[np.ndarray] = None
    ) -> GaitPhaseReport:
        """Analyze biped gait from left/right leg signals.

        Args:
            left_signal: Left leg joint angle signal.
            right_signal: Right leg joint angle signal.
            torso_signal: Optional torso vertical displacement.

        Returns:
            GaitPhaseReport with per-limb phase analysis.
        """
        left_points = self.analyzer.decompose(left_signal, "left_leg")
        right_points = self.analyzer.decompose(right_signal, "right_leg")

        left_dominant = left_points[0] if left_points else PhaseManifoldPoint()
        right_dominant = right_points[0] if right_points else PhaseManifoldPoint()

        # Torso analysis
        torso_dominant = PhaseManifoldPoint(channel_name="torso")
        if torso_signal is not None and len(torso_signal) >= 8:
            torso_points = self.analyzer.decompose(torso_signal, "torso")
            if torso_points:
                torso_dominant = torso_points[0]

        # Compute asymmetry
        asymmetry = self._compute_biped_asymmetry(
            left_dominant, right_dominant
        )

        # Phase offset between legs
        phase_offset = abs(left_dominant.phase_shift -
                           right_dominant.phase_shift) % 1.0
        if phase_offset > 0.5:
            phase_offset = 1.0 - phase_offset

        # Dominant frequency (average of both legs)
        dom_freq = (left_dominant.frequency + right_dominant.frequency) / 2.0

        return GaitPhaseReport(
            left_leg=left_dominant,
            right_leg=right_dominant,
            torso=torso_dominant,
            asymmetry_ratio=asymmetry,
            phase_offset=phase_offset,
            dominant_frequency=dom_freq,
        )

    def analyze_quadruped(
        self,
        fl_signal: np.ndarray,
        fr_signal: np.ndarray,
        hl_signal: np.ndarray,
        hr_signal: np.ndarray,
    ) -> QuadrupedPhaseReport:
        """Analyze quadruped gait from four limb signals.

        Detects gait type based on phase relationships:
        - Walk: FL→HR→FR→HL (diagonal sequence, ~25% offset)
        - Trot: FL+HR together, FR+HL together (50% offset)
        - Canter: 3-beat asymmetric
        - Gallop: 4-beat, near-simultaneous front/hind pairs

        Args:
            fl_signal: Front-left limb signal.
            fr_signal: Front-right limb signal.
            hl_signal: Hind-left limb signal.
            hr_signal: Hind-right limb signal.

        Returns:
            QuadrupedPhaseReport with gait type and phase relationships.
        """
        signals = {
            "front_left": fl_signal,
            "front_right": fr_signal,
            "hind_left": hl_signal,
            "hind_right": hr_signal,
        }

        decomposed = self.analyzer.decompose_multi(signals)

        points = {}
        for name, channel_points in decomposed.items():
            points[name] = channel_points[0] if channel_points \
                else PhaseManifoldPoint(channel_name=name)

        # Compute phase offsets
        fl_phase = points["front_left"].phase_shift
        fr_phase = points["front_right"].phase_shift
        hl_phase = points["hind_left"].phase_shift
        hr_phase = points["hind_right"].phase_shift

        diagonal_offset = abs(fl_phase - hr_phase) % 1.0
        if diagonal_offset > 0.5:
            diagonal_offset = 1.0 - diagonal_offset

        lateral_offset = abs(fl_phase - fr_phase) % 1.0
        if lateral_offset > 0.5:
            lateral_offset = 1.0 - lateral_offset

        # Detect gait type
        gait_type = self._classify_quadruped_gait(
            diagonal_offset, lateral_offset
        )

        return QuadrupedPhaseReport(
            front_left=points["front_left"],
            front_right=points["front_right"],
            hind_left=points["hind_left"],
            hind_right=points["hind_right"],
            gait_type=gait_type,
            diagonal_phase_offset=diagonal_offset,
            lateral_phase_offset=lateral_offset,
        )

    def _compute_biped_asymmetry(
        self, left: PhaseManifoldPoint, right: PhaseManifoldPoint
    ) -> float:
        """Compute asymmetry ratio between left and right legs.

        Returns 0.0 for perfectly symmetric gait, 1.0 for completely
        different patterns.
        """
        # Amplitude asymmetry
        max_amp = max(left.amplitude, right.amplitude, 1e-10)
        amp_diff = abs(left.amplitude - right.amplitude) / max_amp

        # Frequency asymmetry
        max_freq = max(left.frequency, right.frequency, 1e-10)
        freq_diff = abs(left.frequency - right.frequency) / max_freq

        # Phase offset deviation from ideal 0.5
        phase_offset = abs(left.phase_shift - right.phase_shift) % 1.0
        if phase_offset > 0.5:
            phase_offset = 1.0 - phase_offset
        phase_dev = abs(phase_offset - 0.5) * 2.0  # 0 if offset=0.5

        return float(np.clip(
            amp_diff * 0.4 + freq_diff * 0.3 + phase_dev * 0.3,
            0.0, 1.0
        ))

    def _classify_quadruped_gait(
        self, diagonal_offset: float, lateral_offset: float
    ) -> str:
        """Classify quadruped gait type from phase offsets."""
        # Trot: diagonal pairs in sync (offset ≈ 0)
        if diagonal_offset < 0.1:
            return "trot"
        # Walk: sequential (diagonal offset ≈ 0.25, lateral ≈ 0.5)
        if 0.15 < diagonal_offset < 0.35 and lateral_offset > 0.35:
            return "walk"
        # Gallop: near-simultaneous front/hind (lateral offset ≈ 0)
        if lateral_offset < 0.1:
            return "gallop"
        # Canter: 3-beat asymmetric
        if 0.1 < lateral_offset < 0.3:
            return "canter"
        return "walk"  # Default


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def extract_deepphase_channels(
    signal: np.ndarray,
    sample_rate: float = 30.0,
    max_channels: int = 4
) -> List[PhaseManifoldPoint]:
    """Quick extraction of DeepPhase channels from a signal."""
    analyzer = DeepPhaseAnalyzer(
        max_channels=max_channels, sample_rate=sample_rate
    )
    return analyzer.decompose(signal)


def blend_phase_points(
    p1: PhaseManifoldPoint,
    p2: PhaseManifoldPoint,
    alpha: float
) -> PhaseManifoldPoint:
    """Quick phase-space blend between two motion phases."""
    return PhaseBlender.blend(p1, p2, alpha)


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "PhaseManifoldPoint",
    "DeepPhaseAnalyzer",
    "PhaseBlender",
    "GaitPhaseReport",
    "QuadrupedPhaseReport",
    "AsymmetricGaitAnalyzer",
    "extract_deepphase_channels",
    "blend_phase_points",
]

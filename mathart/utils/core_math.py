"""Core math and JSON helpers shared by V6-safe modules."""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence

_EPS = 1e-8


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def vec2(value: Any, default: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
    if isinstance(value, Mapping):
        return (float(value.get("x", default[0])), float(value.get("y", default[1])))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return default


def vec_sub(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] - b[0], a[1] - b[1])


def vec_add(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    return (a[0] + b[0], a[1] + b[1])


def vec_scale(v: tuple[float, float], scale: float) -> tuple[float, float]:
    return (v[0] * scale, v[1] * scale)


def vec_dot(a: tuple[float, float], b: tuple[float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def vec_len(v: tuple[float, float]) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1])


def normalize2(v: tuple[float, float]) -> tuple[float, float]:
    length = vec_len(v)
    if length <= _EPS:
        return (0.0, 0.0)
    return (v[0] / length, v[1] / length)


def quat_identity() -> tuple[float, float, float, float]:
    return (1.0, 0.0, 0.0, 0.0)


def quat_normalize(q: Sequence[float]) -> tuple[float, float, float, float]:
    w, x, y, z = (float(q[0]), float(q[1]), float(q[2]), float(q[3])) if len(q) >= 4 else quat_identity()
    length = math.sqrt(w * w + x * x + y * y + z * z)
    if length <= _EPS:
        return quat_identity()
    return (w / length, x / length, y / length, z / length)


def mat4_identity() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)

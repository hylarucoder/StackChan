"""Keyframe data model mirroring the firmware choreography schema.

The firmware parses a JSON *array* of keyframes in
``stackchan/json/json_helper.cpp::parse_sequence_from_json``. Each keyframe is::

    {
      "leftEye":  {"x":0,"y":0,"rotation":0,"weight":100,"size":0},
      "rightEye": {"x":0,"y":0,"rotation":0,"weight":100,"size":0},
      "mouth":    {"x":0,"y":0,"rotation":0,"weight":100,"size":0},
      "yawServo":   {"angle":300,"speed":200},
      "pitchServo": {"angle":-100,"speed":200},
      "leftRgbColor":"#FF0000",
      "rightRgbColor":"#00FF00",
      "durationMs":800
    }

Servo angles are in units of 0.1 degree (300 == 30 deg). Physical limits come
from ``hal/hal_servo.cpp``:

    yaw   id=1  angleLimit (-1280, 1280)  zero raw 460
    pitch id=2  angleLimit (   30,  870)  zero raw 620

``angle`` is relative to the calibrated zero, so the built-in dances use small
*negative* pitch values to tilt down. We keep generated motion well inside a
conservative band (see ``MotionLimits``) to protect the gears.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


@dataclass(frozen=True)
class MotionLimits:
    """Safe envelope for generated motion (units: 0.1 degree, speed 0-1000).

    Defaults stay inside what the built-in dances use (yaw <= 600, pitch in
    -300..200) rather than the full physical range, so a generated routine is
    safe to run unattended.
    """

    yaw_max: int = 450          # +/- 45 deg ceiling (chorus); verses scale smaller
    pitch_min: int = -200       # -20 deg (tilt down; less "staring at the floor")
    pitch_max: int = 250        # +25 deg (tilt up; used for head-lifts on long notes)
    speed_min: int = 80
    speed_max: int = 1000
    # Hard physical ceiling (never exceeded even if caller widens the band).
    yaw_hard: int = 1280
    pitch_hard_min: int = -400
    pitch_hard_max: int = 870

    def clamp_yaw(self, angle: int) -> int:
        return _clamp(angle, -min(self.yaw_max, self.yaw_hard), min(self.yaw_max, self.yaw_hard))

    def clamp_pitch(self, angle: int) -> int:
        low = max(self.pitch_min, self.pitch_hard_min)
        high = min(self.pitch_max, self.pitch_hard_max)
        return _clamp(angle, low, high)

    def clamp_speed(self, speed: int) -> int:
        return _clamp(speed, self.speed_min, self.speed_max)


@dataclass
class Feature:
    """An avatar feature (eye or mouth). weight/size are 0..100-ish intensities."""

    x: int = 0
    y: int = 0
    rotation: int = 0
    weight: int = 0
    size: int = 0

    def normalized(self) -> Feature:
        return Feature(
            x=_clamp(self.x, -100, 100),
            y=_clamp(self.y, -100, 100),
            rotation=_clamp(self.rotation, -180, 180),
            weight=_clamp(self.weight, 0, 100),
            size=_clamp(self.size, -100, 100),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Servo:
    angle: int = 0
    speed: int = 200

    def to_dict(self) -> dict:
        return {"angle": int(self.angle), "speed": int(self.speed)}


@dataclass
class Keyframe:
    leftEye: Feature = field(default_factory=Feature)
    rightEye: Feature = field(default_factory=Feature)
    mouth: Feature = field(default_factory=Feature)
    yawServo: Servo = field(default_factory=Servo)
    pitchServo: Servo = field(default_factory=Servo)
    leftRgbColor: str | None = None
    rightRgbColor: str | None = None
    durationMs: int = 0

    def to_dict(self) -> dict:
        out: dict = {
            "leftEye": self.leftEye.normalized().to_dict(),
            "rightEye": self.rightEye.normalized().to_dict(),
            "mouth": self.mouth.normalized().to_dict(),
            "yawServo": self.yawServo.to_dict(),
            "pitchServo": self.pitchServo.to_dict(),
            "durationMs": int(self.durationMs),
        }
        # Firmware only reads the color when present; omit when unset to keep
        # the payload small.
        if self.leftRgbColor:
            out["leftRgbColor"] = self.leftRgbColor
        if self.rightRgbColor:
            out["rightRgbColor"] = self.rightRgbColor
        return out


class KeyframeSequence(list):
    """A list of :class:`Keyframe` with JSON helpers and a duration tally."""

    def total_ms(self) -> int:
        return sum(int(kf.durationMs) for kf in self)

    def to_list(self) -> list[dict]:
        return [kf.to_dict() for kf in self]

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_list(), ensure_ascii=False, indent=indent)

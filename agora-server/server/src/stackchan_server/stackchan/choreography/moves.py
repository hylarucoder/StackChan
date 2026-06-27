"""Parametric dance moves.

A *move* maps one beat (its index, its energy 0..1, whether it's a downbeat)
to a single :class:`Keyframe`. The compiler picks a move per section and walks
the beat grid, so every keyframe lands on a beat and the head changes pose on
the beat -- which reads as "dancing to the music".

Conventions:
  * yaw angle in 0.1 deg, + = right, - = left (clamped by MotionLimits)
  * pitch angle in 0.1 deg, + = up, - = down
  * speed scales with energy so loud beats snap and quiet beats glide
  * mouth weight tracks energy as a cheap "singing" proxy
"""

from __future__ import annotations

from typing import Callable

from .schema import Feature, Keyframe, MotionLimits, Servo

# A small, high-contrast palette cycled per bar. Hex strings the firmware reads
# straight into NeonLight::setColor.
PALETTE = ["#FF3030", "#FF8C00", "#FFD500", "#30D030", "#00C0FF", "#8040FF", "#FF40A0"]

# Move signature: (beat_index, energy 0..1, is_downbeat, limits) -> Keyframe
MoveFn = Callable[[int, float, bool, MotionLimits], Keyframe]


def _speed(energy: float, limits: MotionLimits, *, sharp: bool = False) -> int:
    """Map energy to servo speed. Lower = glides; higher = snaps.

    Tuned low so a move eases into its pose over the (now longer) hold instead
    of jerking to the target and sitting still. Accents bias a bit faster.
    """
    # Higher base so the head *arrives* at the pose crisply on the beat instead of
    # still gliding when the beat passes (that lag is what reads as "not 卡点").
    base = 0.55 + 0.40 * energy
    if sharp:
        base = min(1.0, base + 0.18)
    span = limits.speed_max - limits.speed_min
    return limits.clamp_speed(int(limits.speed_min + base * span))


def _amp(energy: float, floor: float = 0.12) -> float:
    """Amplitude scale that tracks energy with a low floor.

    Quiet passages stay subtle (floor of the range), the chorus opens up to full.
    Keeping the move *timing* (density) constant but scaling amplitude this way is
    what makes calm parts read as gentle instead of busy small twitches.
    """
    return floor + (1.0 - floor) * max(0.0, min(1.0, energy))


def _eyes(energy: float, x: int = 0, y: int = 0) -> Feature:
    # Happy squint that opens a little on loud beats; eyes drift with the head.
    return Feature(x=x, y=y, rotation=0, weight=int(45 + 45 * energy))


def _mouth(energy: float) -> Feature:
    # Closed when quiet, open when loud -> looks like singing along.
    return Feature(weight=int(20 + 80 * energy))


def _color(bar_index: int) -> str:
    return PALETTE[bar_index % len(PALETTE)]


# A 2D groove: each entry is a (yaw_dir, pitch_dir) target in [-1, 1].
# yaw_dir: + = right, - = left.  pitch_dir: + = look up, - = look down.
# Cycling through these traces varied diagonal/up/around poses instead of a
# monotonous left-right sway on a single axis.
_GROOVE = [
    (-1.0,  0.8),   # up-left
    ( 0.7, -0.7),   # down-right
    ( 1.0,  0.5),   # up-right
    (-0.5, -0.9),   # down-left
    ( 0.0,  1.0),   # straight up
    ( 0.9,  0.1),   # level right
    (-0.9,  0.6),   # up-left again, different
    ( 0.4, -0.5),   # down-right shallow
]


def _pitch_target(pitch_dir: float, amp: float, limits: MotionLimits) -> int:
    """Map a pitch direction in [-1,1] to an angle, scaled by amplitude."""
    if pitch_dir >= 0:
        return limits.clamp_pitch(int(pitch_dir * amp * limits.pitch_max))
    return limits.clamp_pitch(int(pitch_dir * amp * (-limits.pitch_min)))


# The everyday groove stays modest; accents (heavy/drop beats) pop to full range,
# giving the small-moves-with-big-hits dynamic of a dance track.
GROOVE_SCALE = 0.6


def groove(i: int, energy: float, downbeat: bool, limits: MotionLimits) -> Keyframe:
    """The main groove: step through the 2D pose cycle, scaled by energy.

    Because consecutive moves land in different regions of the (yaw, pitch)
    space, the head dances *around* rather than shaking on one axis. Amplitude is
    kept modest here so the accents stand out.
    """
    yaw_dir, pitch_dir = _GROOVE[i % len(_GROOVE)]
    amp = _amp(energy) * GROOVE_SCALE
    yaw = limits.clamp_yaw(int(yaw_dir * amp * limits.yaw_max))
    pitch = _pitch_target(pitch_dir, amp, limits)
    spd = _speed(energy, limits, sharp=downbeat)
    eye_x = int(6 * yaw_dir * amp)
    eye_y = int(-4 * pitch_dir * amp)  # eyes drift opposite the tilt, a touch
    return Keyframe(
        leftEye=_eyes(energy, x=eye_x, y=eye_y),
        rightEye=_eyes(energy, x=eye_x, y=eye_y),
        mouth=_mouth(energy),
        yawServo=Servo(yaw, spd),
        pitchServo=Servo(pitch, spd),
    )


def look_up(i: int, energy: float, downbeat: bool, limits: MotionLimits) -> Keyframe:
    """Lift the head and hold -- for sustained/long vocal notes ("singing it out").

    Tilts up toward the ceiling with a gentle yaw drift, wide eyes and open mouth.
    Consecutive sustain beats keep choosing this, so the head stays up for the note.
    """
    yaw = limits.clamp_yaw(int(0.18 * limits.yaw_max * (1 if (i % 2 == 0) else -1)))
    # tilt up, scaling with energy
    pitch = limits.clamp_pitch(int((0.6 + 0.4 * energy) * limits.pitch_max))
    spd = _speed(min(energy, 0.55), limits)  # ease up smoothly, don't snap
    return Keyframe(
        leftEye=Feature(x=0, y=-6, weight=int(70 + 30 * energy)),
        rightEye=Feature(x=0, y=-6, weight=int(70 + 30 * energy)),
        mouth=Feature(weight=int(70 + 30 * energy)),  # open, singing the long note
        yawServo=Servo(yaw, spd),
        pitchServo=Servo(pitch, spd),
    )


def accent(i: int, energy: float, downbeat: bool, limits: MotionLimits) -> Keyframe:
    """A bigger, snappier hit -- used on strong downbeats for punctuation."""
    direction = 1 if (i % 4 < 2) else -1
    yaw = limits.clamp_yaw(direction * limits.yaw_max)
    pitch = limits.clamp_pitch(limits.pitch_min)  # duck down on the hit
    spd = limits.clamp_speed(int(0.85 * limits.speed_max + 0.15 * limits.speed_max * energy))
    return Keyframe(
        leftEye=Feature(weight=int(80 + 20 * energy)),
        rightEye=Feature(weight=int(80 + 20 * energy)),
        mouth=Feature(weight=100),
        yawServo=Servo(yaw, spd),
        pitchServo=Servo(pitch, spd),
    )


def home(limits: MotionLimits) -> Keyframe:
    """Neutral centered pose to open/close a routine."""
    return Keyframe(
        leftEye=Feature(weight=100),
        rightEye=Feature(weight=100),
        mouth=Feature(weight=0),
        yawServo=Servo(0, 200),
        pitchServo=Servo(0, 200),
    )


def intro_keyframes(limits: MotionLimits) -> list[Keyframe]:
    """An opening ritual before the dance: settle, lift the head, nod once.

    Gives the routine a deliberate "ready... here we go" feel instead of jumping
    straight into the groove.
    """
    eyes = Feature(weight=100)
    look_up = limits.clamp_pitch(limits.pitch_max)
    nod_down = limits.clamp_pitch(int(limits.pitch_min * 0.6))
    return [
        # settle centered
        Keyframe(leftEye=eyes, rightEye=eyes, mouth=Feature(weight=0),
                 yawServo=Servo(0, 250), pitchServo=Servo(0, 250), durationMs=450),
        # lift the head and hold (抬头)
        Keyframe(leftEye=eyes, rightEye=eyes, mouth=Feature(weight=0),
                 yawServo=Servo(0, 350), pitchServo=Servo(look_up, 350), durationMs=650),
        # a single nod down (点头)
        Keyframe(leftEye=eyes, rightEye=eyes, mouth=Feature(weight=40),
                 yawServo=Servo(0, 650), pitchServo=Servo(nod_down, 650), durationMs=320),
        # recover to center, ready to dance
        Keyframe(leftEye=eyes, rightEye=eyes, mouth=Feature(weight=0),
                 yawServo=Servo(0, 500), pitchServo=Servo(0, 500), durationMs=320),
    ]


# All loudness bands run the same 2D groove; energy scales its amplitude, so
# quiet sections stay subtle and the chorus opens up. Downbeats in louder
# sections may be overridden by accents (see compiler).
SECTION_MOVE: dict[str, MoveFn] = {
    "quiet": groove,
    "mid": groove,
    "loud": groove,
}

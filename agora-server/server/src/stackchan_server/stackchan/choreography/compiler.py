"""Compile AudioFeatures into a KeyframeSequence (the editable choreography)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import moves
from .analysis import AudioFeatures, analyze
from .schema import Keyframe, KeyframeSequence, MotionLimits


@dataclass
class CompileOptions:
    offset_s: float = 0.0
    duration_s: float | None = None
    beats_per_bar: int = 4
    fixed_bpm: float | None = None
    limits: MotionLimits = field(default_factory=MotionLimits)
    # Emit one move every N beats (hold the pose in between). Higher = calmer,
    # less twitchy. 1 = a move every beat (busy), 2 = every other beat, etc.
    beats_per_move: int = 2
    # Add a bigger "accent" hit on the first beat of each bar.
    accent_downbeats: bool = True
    # Cycle eye RGB color once per bar.
    color_bars: bool = True
    # Bracket the routine with a neutral home pose at start and end.
    framing: bool = True
    # Minimum keyframe duration; very short beats are merged up to this to
    # avoid servo thrash and an oversized payload.
    min_duration_ms: int = 90


@dataclass
class Choreography:
    sequence: KeyframeSequence
    features: AudioFeatures
    options: CompileOptions

    def summary(self) -> str:
        f = self.features
        sec_counts: dict[str, int] = {}
        for s in f.section_of_beat:
            sec_counts[s] = sec_counts.get(s, 0) + 1
        sections = ", ".join(f"{k}:{v}" for k, v in sorted(sec_counts.items()))
        return (
            f"tempo={f.tempo_bpm:.1f} BPM  duration={f.duration_s:.1f}s  "
            f"beats={f.n_beats}  keyframes={len(self.sequence)}  "
            f"motion={self.sequence.total_ms() / 1000:.1f}s  sections[{sections}]"
        )


def compile_from_features(features: AudioFeatures, options: CompileOptions) -> Choreography:
    seq = KeyframeSequence()
    limits = options.limits

    n = features.n_beats
    step = max(1, options.beats_per_move)
    # Beat times in ms (absolute, from the start of the analyzed audio).
    bt_ms = [int(t * 1000) for t in features.beat_times]

    # Beat-lock the groove: the opening ritual ends exactly on a downbeat, so the
    # first groove move fires on the beat and every move after it (being an integer
    # number of beats long) stays on the grid -- i.e. it actually "卡点".
    start_beat = 0
    if options.framing:
        intro = moves.intro_keyframes(limits)
        intro_total = sum(kf.durationMs for kf in intro)
        # first downbeat at/after the ritual; else first beat after it; else 0
        start_beat = next(
            (k for k in range(n) if bt_ms[k] >= intro_total and k % features.beats_per_bar == 0),
            next((k for k in range(n) if bt_ms[k] >= intro_total), 0),
        )
        pad = bt_ms[start_beat] - intro_total if start_beat < len(bt_ms) else 0
        if pad > 0:
            intro[-1].durationMs += pad  # stretch the recover-to-center so we land on the beat
        seq.extend(intro)

    move_idx = 0  # counts emitted moves, so the groove pattern advances per MOVE
    for i in range(start_beat, n, step):
        # Mean energy over the held beats; duration runs to the next move's beat
        # (kept on the absolute grid so timing can't drift).
        energy = float(features.beat_energy[i:i + step].mean())
        next_i = min(i + step, n)
        duration = (bt_ms[next_i] - bt_ms[i]) if next_i < n \
            else sum(features.beat_interval_ms(j) for j in range(i, next_i))
        downbeat = features.is_downbeat(i)
        section = features.section_of_beat[i] if i < len(features.section_of_beat) else "mid"
        bar = i // max(1, features.beats_per_bar)
        # Lift the head and hold roughly every other bar in the active sections --
        # lands on phrase points where the long held notes tend to sit.
        lift = downbeat and (bar % 2 == 0) and section != "quiet"

        if lift:
            kf = moves.look_up(move_idx, energy, downbeat, limits)
        elif options.accent_downbeats and downbeat and section != "quiet":
            kf = moves.accent(move_idx, energy, downbeat, limits)
        else:
            move_fn = moves.SECTION_MOVE.get(section, moves.groove)
            kf = move_fn(move_idx, energy, downbeat, limits)

        kf.durationMs = max(options.min_duration_ms, duration)

        if options.color_bars:
            bar = i // max(1, features.beats_per_bar)
            color = moves._color(bar)
            kf.leftRgbColor = color
            kf.rightRgbColor = color

        seq.append(kf)
        move_idx += 1

    if options.framing:
        seq.append(_framed(moves.home(limits), 500))

    return Choreography(sequence=seq, features=features, options=options)


def compile_choreography(path: str, options: CompileOptions | None = None) -> Choreography:
    options = options or CompileOptions()
    features = analyze(
        path,
        offset_s=options.offset_s,
        duration_s=options.duration_s,
        beats_per_bar=options.beats_per_bar,
        fixed_bpm=options.fixed_bpm,
    )
    return compile_from_features(features, options)


def _framed(kf: Keyframe, duration_ms: int) -> Keyframe:
    kf.durationMs = duration_ms
    return kf

"""Audio analysis: MP3 -> beat grid + per-beat energy + coarse sections.

Uses librosa. Kept deliberately small and deterministic: the compiler only
needs (a) where the beats are, (b) how loud each beat is, and (c) a coarse
section label so it can vary the dance style across a song.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SectionLabel = str  # "quiet" | "mid" | "loud"


def _import_librosa():
    """Import librosa lazily so schema/moves/compiler stay importable without it."""
    try:
        import librosa
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "The choreography compiler needs librosa + soundfile. Install with:\n"
            "    uv sync --group choreo"
        ) from exc
    return librosa


@dataclass
class AudioFeatures:
    path: str
    sr: int
    duration_s: float
    tempo_bpm: float
    beat_times: np.ndarray       # shape (N,), seconds of each beat
    beat_energy: np.ndarray      # shape (N,), normalized 0..1 loudness per beat
    beats_per_bar: int           # assumed meter (default 4)
    section_of_beat: list[SectionLabel]  # len N, coarse loudness band per beat
    sustain_of_beat: list[bool]  # len N, True where a long/held note is sounding

    @property
    def n_beats(self) -> int:
        return len(self.beat_times)

    def beat_interval_ms(self, i: int) -> int:
        """Duration of beat i in ms (gap to next beat, or one period at the end)."""
        if i + 1 < len(self.beat_times):
            return int(round((self.beat_times[i + 1] - self.beat_times[i]) * 1000.0))
        if self.tempo_bpm > 0:
            return int(round(60000.0 / self.tempo_bpm))
        return 500

    def is_downbeat(self, i: int) -> bool:
        return self.beats_per_bar > 0 and (i % self.beats_per_bar) == 0


def _band_label(value: float, low: float, high: float) -> SectionLabel:
    if value < low:
        return "quiet"
    if value < high:
        return "mid"
    return "loud"


def _section_labels(beat_energy: np.ndarray, beats_per_bar: int) -> list[SectionLabel]:
    """Smooth per-beat energy over ~2 bars and bucket into quiet/mid/loud.

    Smoothing avoids the style flipping every beat; we want it to track the
    song's structure (verse vs chorus) instead of momentary transients.
    """
    n = len(beat_energy)
    if n == 0:
        return []
    win = max(1, beats_per_bar * 2)
    kernel = np.ones(win) / win
    smoothed = np.convolve(beat_energy, kernel, mode="same")
    # Bucket relative to this song's own dynamics (terciles), so a quiet song
    # still gets a "loud" chorus.
    low, high = np.quantile(smoothed, [0.4, 0.75])
    if high - low < 1e-3:  # nearly flat track
        low, high = 0.33, 0.66
    return [_band_label(float(v), float(low), float(high)) for v in smoothed]


def _sustain_labels(beat_energy, energy_min: float = 0.6,
                    flat_delta: float = 0.16) -> list[bool]:
    """Mark beats sitting on a held note: loud *and* steady.

    A held/long note holds its loudness flat across neighbouring beats, whereas a
    rhythmic hit pulses. So: high energy with low local variation = a sustain.
    Robust on dense mixes (no onset/HPSS needed).
    """
    e = np.asarray(beat_energy, dtype=float)
    n = len(e)
    out: list[bool] = []
    for i in range(n):
        w = e[max(0, i - 1):min(n, i + 2)]
        flat = (float(w.max()) - float(w.min())) < flat_delta
        out.append(bool(e[i] >= energy_min and flat))
    return out


def analyze(
    path: str,
    *,
    sr: int = 22050,
    offset_s: float = 0.0,
    duration_s: float | None = None,
    beats_per_bar: int = 4,
    fixed_bpm: float | None = None,
) -> AudioFeatures:
    """Analyze an audio file into an :class:`AudioFeatures`.

    offset_s/duration_s let you choreograph just a clip (handy for demos and to
    keep the keyframe payload small).
    """
    librosa = _import_librosa()
    y, sr = librosa.load(path, sr=sr, mono=True, offset=offset_s, duration=duration_s)
    if y.size == 0:
        raise ValueError(f"no audio decoded from {path} (offset/duration out of range?)")

    total_s = float(librosa.get_duration(y=y, sr=sr))

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    bpm_arg = float(fixed_bpm) if fixed_bpm else None
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, bpm=bpm_arg, units="frames"
    )
    tempo_bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    if beat_times.size < 2:
        # Fallback: synthesize a grid from tempo so we always have something to
        # dance to even when beat tracking fails on sparse audio.
        period = 60.0 / tempo_bpm if tempo_bpm > 0 else 0.5
        beat_times = np.arange(0.0, total_s, period)
        beat_frames = librosa.time_to_frames(beat_times, sr=sr)

    # Per-beat loudness from RMS, sampled at each beat frame, normalized 0..1.
    rms = librosa.feature.rms(y=y)[0]
    rms_db = librosa.power_to_db(rms ** 2 + 1e-9, ref=np.max)
    rms_db = np.clip((rms_db + 60.0) / 60.0, 0.0, 1.0)  # -60dB..0dB -> 0..1
    beat_idx = np.clip(beat_frames, 0, len(rms_db) - 1)
    beat_energy = rms_db[beat_idx].astype(float)
    if beat_energy.max() > beat_energy.min():
        beat_energy = (beat_energy - beat_energy.min()) / (beat_energy.max() - beat_energy.min())

    sections = _section_labels(beat_energy, beats_per_bar)

    # Held/long-note detection from loudness steadiness (see _sustain_labels).
    sustain = _sustain_labels(beat_energy)

    return AudioFeatures(
        path=path,
        sr=sr,
        duration_s=total_s,
        tempo_bpm=tempo_bpm,
        beat_times=beat_times,
        beat_energy=beat_energy,
        beats_per_bar=beats_per_bar,
        section_of_beat=sections,
        sustain_of_beat=sustain,
    )

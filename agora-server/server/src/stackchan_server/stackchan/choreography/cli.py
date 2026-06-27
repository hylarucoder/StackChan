"""``stackchan-choreograph`` -- compile an MP3 into a dance.json keyframe file.

Examples::

    stackchan-choreograph song.mp3 -o dance.json
    stackchan-choreograph song.mp3 --start 30 --duration 20 --pretty
    stackchan-choreograph song.mp3 --bpm 128 --yaw-max 800
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .compiler import CompileOptions, compile_choreography
from .schema import MotionLimits


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stackchan-choreograph",
        description="Compile an audio file into a StackChan dance.json keyframe sequence.",
    )
    p.add_argument("audio", help="input audio file (mp3/wav/m4a/flac)")
    p.add_argument("-o", "--output", help="output dance.json (default: <audio>.dance.json)")
    p.add_argument("--start", type=float, default=0.0, metavar="SEC",
                   help="start offset in seconds (choreograph a clip)")
    p.add_argument("--duration", type=float, default=None, metavar="SEC",
                   help="length to choreograph in seconds (default: whole file)")
    p.add_argument("--bpm", type=float, default=None,
                   help="force a tempo instead of auto-detecting")
    p.add_argument("--beats-per-bar", type=int, default=4, help="meter (default 4)")
    p.add_argument("--beats-per-move", type=int, default=2,
                   help="one move every N beats; higher = calmer/less twitchy (default 2)")
    p.add_argument("--yaw-max", type=int, default=450, metavar="DECIDEG",
                   help="max |yaw| in 0.1deg units (default 450 = 45deg)")
    p.add_argument("--pitch-min", type=int, default=-200, metavar="DECIDEG")
    p.add_argument("--pitch-max", type=int, default=250, metavar="DECIDEG")
    p.add_argument("--no-accents", action="store_true", help="disable downbeat accents")
    p.add_argument("--no-color", action="store_true", help="disable per-bar RGB")
    p.add_argument("--no-framing", action="store_true", help="omit opening/closing home pose")
    p.add_argument("--pretty", action="store_true", help="indent the JSON output")
    p.add_argument("--quiet", action="store_true", help="don't print the summary")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: audio file not found: {audio_path}", file=sys.stderr)
        return 2

    out_path = Path(args.output) if args.output else audio_path.with_suffix(".dance.json")

    limits = MotionLimits(yaw_max=args.yaw_max, pitch_min=args.pitch_min, pitch_max=args.pitch_max)
    options = CompileOptions(
        offset_s=args.start,
        duration_s=args.duration,
        beats_per_bar=args.beats_per_bar,
        beats_per_move=args.beats_per_move,
        fixed_bpm=args.bpm,
        limits=limits,
        accent_downbeats=not args.no_accents,
        color_bars=not args.no_color,
        framing=not args.no_framing,
    )

    try:
        choreo = compile_choreography(str(audio_path), options)
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"error: {exc}", file=sys.stderr)
        return 1

    payload = choreo.sequence.to_json(indent=2 if args.pretty else None)
    out_path.write_text(payload, encoding="utf-8")

    if not args.quiet:
        print(choreo.summary())
        print(f"wrote {len(choreo.sequence)} keyframes -> {out_path} "
              f"({out_path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

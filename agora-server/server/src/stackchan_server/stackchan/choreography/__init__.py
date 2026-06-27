"""MP3 -> StackChan choreography compiler.

Turns a music file into a ``dance.json`` keyframe sequence that the firmware
can play directly (``animation::parse_sequence_from_json`` -> ``DanceModifier``,
or the ``0x14 DanceSequence`` WebSocket channel).

The pipeline is:

    analysis.analyze(mp3)  ->  AudioFeatures (beat grid + per-beat energy + sections)
    compiler.compile(...)  ->  KeyframeSequence  (a list of Keyframe)
    sequence.to_json()     ->  JSON array string the firmware parses

See ``cli.py`` for the ``stackchan-choreograph`` command.
"""

from .compiler import CompileOptions, compile_choreography
from .schema import Feature, Keyframe, KeyframeSequence, Servo

__all__ = [
    "Feature",
    "Servo",
    "Keyframe",
    "KeyframeSequence",
    "CompileOptions",
    "compile_choreography",
]

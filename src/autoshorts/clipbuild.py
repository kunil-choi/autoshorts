"""Turn one or more transcript ranges the worker picked (step 6) into a
single hard-cut source clip, ready for render.py.

Only straight cuts are supported (no crossfades/transitions) - each range
is extracted from the MediaSource at its native quality, then concatenated
in the order the worker selected them.
"""

from __future__ import annotations

from pathlib import Path

from .analyze import ClipRange
from .sources.base import MediaSource


def build_clip(source: MediaSource, ranges: list[ClipRange], out_path: Path) -> Path:
    """Extract each range via source.extract_clip() and hard-cut concat
    them (ffmpeg concat demuxer) into a single video at out_path. A single
    range just extracts directly, no concat step needed."""
    raise NotImplementedError

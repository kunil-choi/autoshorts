"""Turn one or more transcript ranges the worker picked (step 6) into a
single hard-cut source clip, ready for render.py.

Only straight cuts are supported (no crossfades/transitions) - each range
is extracted from the MediaSource at its native quality via
MediaSource.extract_clip() (which returns exactly that range, no padding),
then concatenated in the order the worker selected them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .analyze import ClipRange
from .sources.base import MediaSource


def build_clip(
    source: MediaSource, ranges: list[ClipRange], out_path: Path, work_dir: Path | None = None
) -> Path:
    if not ranges:
        raise ValueError("at least one range is required")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = work_dir or out_path.parent / f"_{out_path.stem}_parts"
    work_dir.mkdir(parents=True, exist_ok=True)

    part_paths = [
        source.extract_clip(r.start_sec, r.end_sec, work_dir / f"part{i}.mp4")
        for i, r in enumerate(ranges)
    ]

    if len(part_paths) == 1:
        shutil.move(str(part_paths[0]), str(out_path))
        return out_path

    concat_list = work_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in part_paths), encoding="utf-8"
    )
    # every part came out of the same extract_clip() encode settings
    # (libx264 crf 15 / aac 256k), so a stream-copy concat is safe and
    # avoids yet another re-encode generation before render.py's own pass.
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    return out_path

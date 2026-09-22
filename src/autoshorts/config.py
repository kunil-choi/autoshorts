"""App-wide settings.

Unlike radihola (autoshorts' predecessor), there is no hardcoded list of
programs/channels here - every job starts from a URL or an uploaded file
picked by the worker, so nothing channel-specific needs to live in code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ClipLengthPreset:
    key: str
    label: str
    min_sec: int
    max_sec: int


CLIP_LENGTH_PRESETS: tuple[ClipLengthPreset, ...] = (
    ClipLengthPreset("1min", "1분 이내", 10, 60),
    ClipLengthPreset("2min", "2분 이내", 10, 120),
    ClipLengthPreset("3min", "3분 이내", 10, 180),
)

DEFAULT_CLIP_LENGTH_PRESET = "2min"


def get_clip_length_preset(key: str) -> ClipLengthPreset:
    for preset in CLIP_LENGTH_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError(f"unknown clip length preset '{key}'")


# .env ships AUTOSHORTS_MODEL= blank as a template - os.environ.get's
# default only kicks in when the key is *absent*, not when python-dotenv
# has set it to an empty string, so `or` (not the two-arg .get form) is
# what actually makes a blank .env line fall back to this default.
MODEL = os.environ.get("AUTOSHORTS_MODEL") or "claude-sonnet-5"

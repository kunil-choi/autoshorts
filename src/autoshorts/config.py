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


MODEL = os.environ.get("AUTOSHORTS_MODEL", "claude-sonnet-5")

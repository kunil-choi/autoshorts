"""App-wide settings.

The manual work paths (review-and-confirm, and the one-off "완전 자동
모드" button) still take any URL/file a worker hands them - nothing
channel-specific is required there. PROGRAMS below is additive: it's only
consulted by the scheduler (scheduler.py), which watches a fixed, small set
of regularly-broadcast radio programs' YouTube playlists and kicks off the
automatic pipeline on its own once each day's VOD is up, without a worker
pasting anything in.
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


@dataclass(frozen=True)
class ProgramConfig:
    key: str  # also the default_assets/<key>/ subfolder name for this
    # program's logos (see server.py's _find_default_asset)
    name: str
    playlist_url: str
    # local time (Asia/Seoul - this is a KBS Seoul radio schedule) the
    # broadcast normally ends. The scheduler starts checking the playlist
    # check_delay_min after this, and keeps checking periodically until
    # that day's VOD shows up (see scheduler.py for the retry/give-up
    # window) - the VOD doesn't always land exactly on time.
    broadcast_end_hour: int
    broadcast_end_minute: int = 0
    check_delay_min: int = 30
    weekdays_only: bool = True
    clip_length_key: str = DEFAULT_CLIP_LENGTH_PRESET
    count: int = 5
    # True for the two KBS 1 Radio programs: top-left gets this program's
    # (or the shared) logo_left, top-right gets logo_right (KBS 1라디오).
    # False (currently only 머니올라) skips the top-right logo entirely -
    # that channel isn't a KBS radio co-brand, it only shows its own single
    # logo in the top-left slot (see server.py's _run_auto_pipeline).
    show_top_right_logo: bool = True


PROGRAMS: tuple[ProgramConfig, ...] = (
    ProgramConfig(
        key="leedaeho",
        name="성공예감 이대호입니다",
        playlist_url="https://www.youtube.com/playlist?list=PLFnESzVU01TEw0-QDaxnuLcKKoxlMiQpF",
        broadcast_end_hour=11,
    ),
    ProgramConfig(
        key="economy_show",
        name="경제쇼",
        playlist_url="https://www.youtube.com/playlist?list=PLFnESzVU01TG3D5Gj2yrv21vkLiHS7If8",
        broadcast_end_hour=17,
    ),
)

# 머니올라 is a manual-link-only source (no fixed broadcast schedule, so the
# scheduler never watches it - deliberately left out of PROGRAMS above).
# broadcast_end_hour/weekdays_only are irrelevant here and just carry their
# defaults.
MONEYOLA = ProgramConfig(
    key="moneyola",
    name="머니올라",
    playlist_url="",
    broadcast_end_hour=0,
    show_top_right_logo=False,
)

# Superset used for looking a program up by key (manual "완전 자동 모드"
# dropdown, default_assets resolution). The scheduler loop must keep using
# PROGRAMS directly, not this, or it would start polling 머니올라 too.
ALL_PROGRAMS: tuple[ProgramConfig, ...] = PROGRAMS + (MONEYOLA,)


def get_program(key: str) -> ProgramConfig:
    for program in ALL_PROGRAMS:
        if program.key == key:
            return program
    raise ValueError(f"unknown program '{key}'")

"""Watches a small, fixed set of radio programs' YouTube playlists
(config.PROGRAMS) and kicks off the automatic shorts pipeline on its own
once each weekday's VOD shows up - no worker needs to paste a link in for
these two programs. This is additive to, and independent of, the existing
manual paths (review-and-confirm, and the one-off "완전 자동 모드" button),
which still work exactly as before for any other video.

Each program broadcasts on a fixed schedule (weekdays only) and its VOD is
expected on the program's playlist shortly after the broadcast ends, but
not at a perfectly predictable minute - so the approach is: start checking
check_delay_min after the scheduled end time, and keep checking every
POLL_INTERVAL_SEC until a new video shows up or GIVE_UP_AFTER_HOURS passes,
whichever comes first.

State survives app restarts via a small JSON file (scheduler_state.json in
the work directory) tracking, per program: the last video id seen (so a
restart doesn't try to reprocess something already handled, and so a video
already sitting on the playlist before this feature's first run isn't
mistaken for "new") and whether today has already been handled (so it
doesn't reprocess the same day's video repeatedly, or keep retrying after
giving up).
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import yt_dlp

from .config import PROGRAMS, ProgramConfig
# reusing youtube_source's bot-blocking mitigations (cookies file, player
# client fallback order - see its own docstring) instead of duplicating
# them here; there's no separate `yt-dlp` CLI binary in the frozen build,
# only the yt_dlp Python package, so this has to go through the same
# YoutubeDL API youtube_source.py already uses, not a subprocess call.
from .sources.youtube_source import _base_ydl_opts

KST = ZoneInfo("Asia/Seoul")

# how long after the trigger time to keep retrying before giving up on a
# program for the day - the VOD doesn't always land exactly check_delay_min
# after the broadcast ends.
GIVE_UP_AFTER_HOURS = 4
POLL_INTERVAL_SEC = 600  # 10 minutes


@dataclass(frozen=True)
class PlaylistVideo:
    id: str
    url: str
    title: str


OnNewVideo = Callable[[ProgramConfig, PlaylistVideo], Awaitable[None]]


def _state_path(work_dir: Path) -> Path:
    return work_dir / "scheduler_state.json"


def _load_state(work_dir: Path) -> dict:
    path = _state_path(work_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(work_dir: Path, state: dict) -> None:
    path = _state_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_playlist_video(playlist_url: str) -> PlaylistVideo | None:
    """Return the most recently added video on playlist_url, or None on any
    failure (network hiccup, yt-dlp error, empty playlist) - never raises,
    since the scheduler loop just tries again on its next poll.
    """
    try:
        opts = _base_ydl_opts({"extract_flat": True, "playlist_items": "1"})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
        entries = (info or {}).get("entries") or []
        if not entries or not entries[0].get("id"):
            return None
        entry = entries[0]
        video_id = entry["id"]
        return PlaylistVideo(
            id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=entry.get("title") or "",
        )
    except Exception as e:  # noqa: BLE001 - a transient fetch failure must never take the scheduler loop down
        print(f"[scheduler] {playlist_url} 확인 실패: {e}")
        return None


def trigger_datetime(program: ProgramConfig, on_date: date) -> datetime:
    end = datetime(
        on_date.year, on_date.month, on_date.day,
        program.broadcast_end_hour, program.broadcast_end_minute, tzinfo=KST,
    )
    return end + timedelta(minutes=program.check_delay_min)


def is_broadcast_day(program: ProgramConfig, on_date: date) -> bool:
    return not (program.weekdays_only and on_date.weekday() >= 5)  # Sat=5, Sun=6


def should_check_now(program: ProgramConfig, now: datetime) -> bool:
    """Whether now falls inside today's "keep polling" window for this
    program: on a broadcast day, past the trigger time, within the
    give-up window."""
    if not is_broadcast_day(program, now.date()):
        return False
    trigger_at = trigger_datetime(program, now.date())
    return trigger_at <= now <= trigger_at + timedelta(hours=GIVE_UP_AFTER_HOURS)


def has_given_up_for_today(program: ProgramConfig, now: datetime) -> bool:
    if not is_broadcast_day(program, now.date()):
        return False
    return now > trigger_datetime(program, now.date()) + timedelta(hours=GIVE_UP_AFTER_HOURS)


async def _poll_once(
    work_dir: Path, state: dict, program: ProgramConfig, now: datetime, on_new_video: OnNewVideo,
) -> None:
    today = now.date().isoformat()
    prog_state = state.setdefault(program.key, {})
    if prog_state.get("handled_date") == today:
        return  # already produced today's shorts, or already gave up today

    if not should_check_now(program, now):
        if has_given_up_for_today(program, now):
            print(f"[scheduler] {program.name}: 오늘({today}) 영상을 찾지 못해 포기합니다.")
            prog_state["handled_date"] = today
            prog_state["result"] = "not_found"
            _save_state(work_dir, state)
        return

    video = latest_playlist_video(program.playlist_url)
    if video is None:
        return

    # first time we've ever seen this program's playlist (fresh install, or
    # this feature's first run): record the current top video as the
    # baseline without treating it as "new" - it may well be a video from
    # before this feature existed, not something that just appeared.
    if "last_video_id" not in prog_state:
        prog_state["last_video_id"] = video.id
        _save_state(work_dir, state)
        return

    if video.id == prog_state["last_video_id"]:
        return  # nothing new yet - keep polling

    print(f"[scheduler] {program.name}: 새 영상 발견 - {video.title} ({video.id})")
    prog_state["last_video_id"] = video.id
    prog_state["handled_date"] = today
    prog_state["result"] = "processing"
    _save_state(work_dir, state)
    try:
        await on_new_video(program, video)
        prog_state["result"] = "done"
    except Exception:  # noqa: BLE001 - one program failing must not kill the loop
        traceback.print_exc()
        prog_state["result"] = "error"
    _save_state(work_dir, state)


async def run_scheduler_loop(
    work_dir: Path,
    on_new_video: OnNewVideo,
    programs: tuple[ProgramConfig, ...] = PROGRAMS,
    poll_interval_sec: int = POLL_INTERVAL_SEC,
) -> None:
    """Runs forever as a background task (started at app startup - see
    webui/server.py). Never raises; a single program's failure in one
    iteration is logged and the loop just continues on its next poll.
    """
    state = _load_state(work_dir)
    while True:
        now = datetime.now(KST)
        for program in programs:
            try:
                await _poll_once(work_dir, state, program, now, on_new_video)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        await asyncio.sleep(poll_interval_sec)

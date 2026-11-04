"""Watches a small, fixed set of radio programs' YouTube playlists
(config.PROGRAMS) and kicks off the automatic shorts pipeline on its own
once each weekday's VOD(s) show up - no worker needs to paste a link in for
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
import re
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
from .sources.youtube_source import YouTubeSource, _base_ydl_opts

KST = ZoneInfo("Asia/Seoul")

# how long after the trigger time to keep retrying before giving up on a
# program for the day - the VOD doesn't always land exactly check_delay_min
# after the broadcast ends.
GIVE_UP_AFTER_HOURS = 4
POLL_INTERVAL_SEC = 600  # 10 minutes
# how many of the playlist's newest videos to look at per poll. Some
# programs (이대호) upload each day as separate 1부/2부 videos, so more than
# one new video can show up per day.
PLAYLIST_SCAN_COUNT = 5
SEEN_IDS_KEEP = 50


@dataclass(frozen=True)
class PlaylistVideo:
    id: str
    url: str
    title: str


def part_key(title: str) -> str:
    """Which episode of the day a video is: "1" for 1부, "2" for 2부, or
    "single" when the title carries no 부 marker. Only one video per part per
    day is processed."""
    m = re.search(r"([1-9])\s*부", title)
    return m.group(1) if m else "single"


def video_broadcast_date(video_id: str) -> date | None:
    """The (KST) date the video was published, or None if it can't be
    determined right now (never raises - the scheduler retries next poll)."""
    try:
        raw = YouTubeSource(video_id).upload_date
        return datetime.strptime(raw, "%Y%m%d").date() if raw else None
    except Exception as e:  # noqa: BLE001
        print(f"[scheduler] 영상 날짜 확인 실패({video_id}): {e}")
        return None


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


def latest_playlist_videos(playlist_url: str, count: int = PLAYLIST_SCAN_COUNT) -> list[PlaylistVideo]:
    """Return the newest `count` videos on playlist_url, newest first, or an
    empty list on any failure (network hiccup, yt-dlp error, empty
    playlist) - never raises, since the scheduler loop just tries again on
    its next poll.
    """
    try:
        opts = _base_ydl_opts({"extract_flat": True, "playlist_items": f"1-{count}"})
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
        videos = []
        for entry in (info or {}).get("entries") or []:
            if not entry or not entry.get("id"):
                continue
            videos.append(PlaylistVideo(
                id=entry["id"],
                url=f"https://www.youtube.com/watch?v={entry['id']}",
                title=entry.get("title") or "",
            ))
        return videos
    except Exception as e:  # noqa: BLE001 - a transient fetch failure must never take the scheduler loop down
        print(f"[scheduler] {playlist_url} 확인 실패: {e}")
        return []


def latest_playlist_video(playlist_url: str) -> PlaylistVideo | None:
    """The single newest video on playlist_url (None on failure/empty)."""
    videos = latest_playlist_videos(playlist_url, 1)
    return videos[0] if videos else None


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
        return  # today's polling window is already closed

    if not should_check_now(program, now):
        if has_given_up_for_today(program, now):
            # window over. If shorts were made today, keep that result;
            # otherwise record that nothing showed up.
            if prog_state.get("processed_date") == today:
                print(f"[scheduler] {program.name}: 오늘({today}) 감시를 마칩니다.")
            else:
                print(f"[scheduler] {program.name}: 오늘({today}) 영상을 찾지 못해 포기합니다.")
                prog_state["result"] = "not_found"
            prog_state["handled_date"] = today
            _save_state(work_dir, state)
        return

    videos = latest_playlist_videos(program.playlist_url)
    if not videos:
        return

    # videos already handled (or present before this feature's first run).
    # "last_video_id" is the older single-video state format.
    if "seen_ids" not in prog_state:
        prog_state["seen_ids"] = [prog_state["last_video_id"]] if "last_video_id" in prog_state else []
        if not prog_state["seen_ids"]:
            # first time we've ever seen this program's playlist: record
            # what's there as the baseline without treating it as "new" -
            # it may well be from before this feature existed.
            prog_state["seen_ids"] = [v.id for v in videos]
            prog_state["last_video_id"] = videos[0].id
            _save_state(work_dir, state)
            return
    seen = prog_state["seen_ids"]

    # Only videos published today (broadcast day) are handled, and only ONE
    # video per part (1부 / 2부) per day. Anything older that merely isn't in
    # seen_ids yet (app was off, state lost, ...) is marked seen and skipped.
    done_parts = prog_state.get("processed_parts", {})
    if done_parts.get("date") != today:
        done_parts = {"date": today, "parts": []}
        prog_state["processed_parts"] = done_parts
    parts_done: list[str] = done_parts["parts"]

    todo: list[tuple[str, PlaylistVideo]] = []  # newest first
    for video in videos:
        if video.id in seen:
            continue
        published = video_broadcast_date(video.id)
        if published is None:
            continue  # unknown date: don't mark seen, look again next poll
        if published != now.date():
            print(f"[scheduler] {program.name}: 오늘 영상이 아니라 건너뜀 - {video.title} ({published})")
            seen.append(video.id)
            continue
        key = part_key(video.title)
        if key in parts_done or any(k == key for k, _ in todo):
            print(f"[scheduler] {program.name}: 같은 파트({key}) 영상이 이미 있어 건너뜀 - {video.title}")
            seen.append(video.id)
            continue
        todo.append((key, video))
    del seen[:-SEEN_IDS_KEEP]
    _save_state(work_dir, state)

    for key, video in reversed(todo):  # oldest first (1부 before 2부)
        print(f"[scheduler] {program.name}: 새 영상 발견 - {video.title} ({video.id})")
        seen.append(video.id)
        del seen[:-SEEN_IDS_KEEP]
        parts_done.append(key)
        prog_state["last_video_id"] = video.id
        prog_state["processed_date"] = today
        prog_state["result"] = "processing"
        _save_state(work_dir, state)
        try:
            await on_new_video(program, video)
            prog_state["result"] = "done"
        except Exception:  # noqa: BLE001 - one video failing must not stop the rest or kill the loop
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

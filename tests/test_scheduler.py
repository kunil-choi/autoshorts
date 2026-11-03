import asyncio
from datetime import date, datetime, timedelta

from autoshorts import scheduler
from autoshorts.config import ProgramConfig

KST = scheduler.KST


def _program(**overrides):
    defaults = dict(
        key="test_prog", name="테스트 프로그램",
        playlist_url="https://example.com/playlist",
        broadcast_end_hour=11, broadcast_end_minute=0,
        check_delay_min=30, weekdays_only=True,
        clip_length_key="2min", count=5,
    )
    defaults.update(overrides)
    return ProgramConfig(**defaults)


def test_trigger_datetime_adds_check_delay():
    program = _program(broadcast_end_hour=11, broadcast_end_minute=0, check_delay_min=30)
    dt = scheduler.trigger_datetime(program, date(2026, 10, 26))
    assert dt == datetime(2026, 10, 26, 11, 30, tzinfo=KST)


def test_is_broadcast_day_excludes_weekends():
    program = _program()
    # 2026-10-26 is a Monday; 2026-10-24/25 are Sat/Sun
    assert scheduler.is_broadcast_day(program, date(2026, 10, 26)) is True
    assert scheduler.is_broadcast_day(program, date(2026, 10, 24)) is False
    assert scheduler.is_broadcast_day(program, date(2026, 10, 25)) is False


def test_should_check_now_window():
    program = _program(broadcast_end_hour=11, check_delay_min=30)
    trigger = datetime(2026, 10, 26, 11, 30, tzinfo=KST)
    assert scheduler.should_check_now(program, trigger - timedelta(minutes=1)) is False
    assert scheduler.should_check_now(program, trigger) is True
    assert scheduler.should_check_now(program, trigger + timedelta(hours=3)) is True
    assert scheduler.should_check_now(program, trigger + timedelta(hours=5)) is False


def test_should_check_now_false_on_weekend():
    program = _program(broadcast_end_hour=11, check_delay_min=30)
    saturday_trigger = datetime(2026, 10, 24, 11, 30, tzinfo=KST)
    assert scheduler.should_check_now(program, saturday_trigger) is False


def test_has_given_up_for_today():
    program = _program(broadcast_end_hour=11, check_delay_min=30)
    trigger = datetime(2026, 10, 26, 11, 30, tzinfo=KST)
    assert scheduler.has_given_up_for_today(program, trigger + timedelta(hours=3)) is False
    assert scheduler.has_given_up_for_today(program, trigger + timedelta(hours=5)) is True


def test_poll_once_seeds_baseline_without_triggering(tmp_path, monkeypatch):
    program = _program()
    calls = []

    async def fake_on_new_video(prog, video):
        calls.append(video)

    video = scheduler.PlaylistVideo(id="abc123", url="https://x/abc123", title="t")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [video])

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert calls == []
    assert state[program.key]["last_video_id"] == "abc123"
    assert "handled_date" not in state[program.key]


def test_poll_once_triggers_on_new_video_after_baseline(tmp_path, monkeypatch):
    program = _program()
    calls = []

    async def fake_on_new_video(prog, video):
        calls.append(video)

    new_video = scheduler.PlaylistVideo(id="new", url="https://x/new", title="new ep")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [new_video])

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {program.key: {"last_video_id": "old"}}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert len(calls) == 1 and calls[0].id == "new"
    assert state[program.key]["last_video_id"] == "new"
    assert state[program.key]["processed_date"] == now.date().isoformat()
    # polling window stays open so a later 2부 upload is still picked up
    assert "handled_date" not in state[program.key]
    assert state[program.key]["result"] == "done"


def test_poll_once_skips_unchanged_video(tmp_path, monkeypatch):
    program = _program()
    calls = []

    async def fake_on_new_video(prog, video):
        calls.append(video)

    same_video = scheduler.PlaylistVideo(id="same", url="https://x/same", title="t")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [same_video])

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {program.key: {"last_video_id": "same"}}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert calls == []
    assert "handled_date" not in state[program.key]


def test_poll_once_skips_if_already_handled_today(tmp_path, monkeypatch):
    program = _program()
    calls = []

    async def fake_on_new_video(prog, video):
        calls.append(video)

    # a different video is now available, but today is already marked handled
    different_video = scheduler.PlaylistVideo(id="different", url="https://x/d", title="t")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [different_video])

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    today = now.date().isoformat()
    state = {program.key: {"last_video_id": "old", "handled_date": today, "result": "done"}}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert calls == []
    assert state[program.key]["last_video_id"] == "old"


def test_poll_once_does_nothing_before_trigger_time(tmp_path, monkeypatch):
    program = _program()
    poll_called = []
    monkeypatch.setattr(
        scheduler, "latest_playlist_videos",
        lambda url: poll_called.append(url) or [],
    )

    async def fake_on_new_video(prog, video):
        raise AssertionError("should not be called before the trigger time")

    now = scheduler.trigger_datetime(program, date(2026, 10, 26)) - timedelta(hours=1)
    state = {}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert poll_called == []  # never even checked the playlist
    assert state[program.key] == {}


def test_poll_once_marks_given_up_without_polling_playlist(tmp_path, monkeypatch):
    program = _program()
    poll_called = []
    monkeypatch.setattr(
        scheduler, "latest_playlist_videos",
        lambda url: poll_called.append(url) or [],
    )

    async def fake_on_new_video(prog, video):
        raise AssertionError("should not be called once given up for today")

    now = scheduler.trigger_datetime(program, date(2026, 10, 26)) + timedelta(hours=5)
    state = {}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert poll_called == []
    assert state[program.key]["handled_date"] == now.date().isoformat()
    assert state[program.key]["result"] == "not_found"


def test_poll_once_records_error_without_raising(tmp_path, monkeypatch):
    program = _program()

    new_video = scheduler.PlaylistVideo(id="new", url="https://x/new", title="t")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [new_video])

    async def failing_on_new_video(prog, video):
        raise RuntimeError("boom")

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {program.key: {"last_video_id": "old"}}
    # must not raise - a failure processing one program must not crash the
    # scheduler loop for the rest
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, failing_on_new_video))

    assert state[program.key]["result"] == "error"
    assert state[program.key]["processed_date"] == now.date().isoformat()


def test_poll_once_handles_part1_and_part2_on_the_same_day(tmp_path, monkeypatch):
    program = _program()
    part1 = scheduler.PlaylistVideo(id="p1", url="https://x/p1", title="1부")
    part2 = scheduler.PlaylistVideo(id="p2", url="https://x/p2", title="2부")
    playlist = [part1]  # newest first
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: list(playlist))

    handled = []

    async def fake_on_new_video(prog, video):
        handled.append(video.id)

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {program.key: {"last_video_id": "old"}}

    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))
    assert handled == ["p1"]

    # nothing new on the next poll
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now + timedelta(minutes=10), fake_on_new_video))
    assert handled == ["p1"]

    # 2부 is uploaded later the same day
    playlist.insert(0, part2)
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now + timedelta(minutes=20), fake_on_new_video))
    assert handled == ["p1", "p2"]


def test_poll_once_handles_both_parts_uploaded_together_oldest_first(tmp_path, monkeypatch):
    program = _program()
    part1 = scheduler.PlaylistVideo(id="p1", url="https://x/p1", title="1부")
    part2 = scheduler.PlaylistVideo(id="p2", url="https://x/p2", title="2부")
    old = scheduler.PlaylistVideo(id="old", url="https://x/old", title="어제")
    monkeypatch.setattr(scheduler, "latest_playlist_videos", lambda url: [part2, part1, old])

    handled = []

    async def fake_on_new_video(prog, video):
        handled.append(video.id)

    now = scheduler.trigger_datetime(program, date(2026, 10, 26))
    state = {program.key: {"last_video_id": "old"}}
    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))

    assert handled == ["p1", "p2"]  # 1부 before 2부, yesterday's video untouched


def test_poll_once_keeps_done_result_when_window_closes(tmp_path):
    program = _program()
    now = scheduler.trigger_datetime(program, date(2026, 10, 26)) + timedelta(hours=5)
    today = now.date().isoformat()
    state = {program.key: {"processed_date": today, "result": "done"}}

    async def fake_on_new_video(prog, video):
        raise AssertionError("not expected")

    asyncio.run(scheduler._poll_once(tmp_path, state, program, now, fake_on_new_video))
    assert state[program.key]["result"] == "done"
    assert state[program.key]["handled_date"] == today

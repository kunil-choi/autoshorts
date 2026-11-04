"""Local web app for autoshorts.

Run with: uvicorn webui.server:app --reload --port 8787

Runs on the worker's own machine (not a shared server) so downloads and
rendering happen locally - see the project README for why. One screen,
"자동 생성" (/auto), with two ways to make shorts from a YouTube link:

- staged (✂️ 편집 시작): analysis proposes 5 candidates (hook 3 + substantive
  2); the worker edits each block's caption lines (deleting a line cuts that
  footage), watches a confirmation render, then uploads privately. State is
  persisted by editjob.py so jobs survive restarts.
- fully automatic (⚡ 완전 자동): the same analysis, then render + private
  upload of all 5 with no editing (AUTO_JOBS, in memory).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import shutil
import sys
import traceback
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# a PyInstaller-frozen (onedir) build has two distinct base paths, and
# conflating them was a real bug caught by test-building this spec: bundled
# read-only data (templates/, static/ - see installer/autoshorts.spec)
# lands under sys._MEIPASS (PyInstaller >= 6 puts this in an _internal/
# subfolder next to the exe), while user-facing/writable things (.env,
# work/, ffmpeg_bin/ - see installer/launcher.py) belong directly next to
# the exe itself, i.e. sys.executable's directory. __file__ points inside
# the frozen bundle's internals and isn't usable as a base path at all in
# this mode. Dev mode (plain `uvicorn webui.server:app`) is unaffected -
# it keeps using __file__ exactly as before.
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).resolve().parent
    WEBUI_DIR = Path(sys._MEIPASS)
else:
    WEBUI_DIR = Path(__file__).resolve().parent
    REPO_ROOT = WEBUI_DIR.parent
    SRC_DIR = REPO_ROOT / "src"
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

load_dotenv(REPO_ROOT / ".env")

from autoshorts import analyze, clipbuild, editjob, library, render, scheduler, youtube_upload  # noqa: E402
from autoshorts.config import (  # noqa: E402
    ALL_PROGRAMS, CLIP_LENGTH_PRESETS, DEFAULT_CLIP_LENGTH_PRESET, MONEYOLA, PROGRAMS, ProgramConfig,
    get_clip_length_preset, get_program,
)
from autoshorts.scheduler import KST  # noqa: E402
from autoshorts.sources import UploadedFileSource, YouTubeSource  # noqa: E402
from autoshorts.transcript import get_transcript_for_source  # noqa: E402

WORK_DIR = REPO_ROOT / "work"
WORK_DIR.mkdir(exist_ok=True)

# "완전 자동 모드" (see create_auto_job below) needs images for every clip
# it produces without a worker there to upload any - default_assets/ is a
# user-maintained folder (see its README.txt) a department admin drops a
# fixed logo/banner set into once, reused for every auto-run.
DEFAULT_ASSETS_DIR = REPO_ROOT / "default_assets"

# YouTube Data API OAuth client - see youtube_upload.py. Defaults to files
# sitting next to .env so there's nothing extra to configure for the common
# case; overridable via env var for a non-standard layout.
YOUTUBE_CLIENT_SECRET_FILE = Path(
    os.environ.get("YOUTUBE_CLIENT_SECRET_FILE") or str(REPO_ROOT / "client_secret.json")
)
YOUTUBE_TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE") or str(REPO_ROOT / "youtube_token.json"))


def _find_default_asset(name: str, program_key: str | None = None) -> Path | None:
    """Look up a default_assets image, preferring a program-specific one
    (default_assets/<program_key>/<name>.*) and falling back to the shared
    one at default_assets/<name>.* - see default_assets/README.txt."""
    search_dirs = ([DEFAULT_ASSETS_DIR / program_key] if program_key else []) + [DEFAULT_ASSETS_DIR]
    for directory in search_dirs:
        for ext in (".png", ".jpg", ".jpeg"):
            path = directory / f"{name}{ext}"
            if path.is_file():
                return path
    return None


# optional: a playlist on the worker's own channel (e.g. a "라디올라" corner
# playlist) every auto-uploaded short also gets added to, in addition to
# just sitting on the channel. Skipped entirely if unset.
RADIHOLA_PLAYLIST_ID = os.environ.get("RADIHOLA_PLAYLIST_ID") or ""

app = FastAPI(title="autoshorts")
templates = Jinja2Templates(directory=str(WEBUI_DIR / "templates"))
app.mount(
    "/static", StaticFiles(directory=str(WEBUI_DIR / "static")), name="static"
)

# 완전 자동 모드 (see create_auto_job): source -> candidates -> (build,
# correct captions, render, upload) per candidate, all unattended.
AUTO_JOBS: dict[str, dict] = {}


def _auto_job_work_dir(auto_job_id: str) -> Path:
    return WORK_DIR / "auto_jobs" / auto_job_id


# ---------------------------------------------------------------------
# 화면 구성: 자동 생성(/auto, 첫 화면) 하나.
#   - 단계별 작업(✂️ 편집 시작): 후보 5개 분석 → 자막 편집(줄 삭제 = 영상 컷)
#     → 확인 영상 → 비공개 업로드. 상태는 work/edit_jobs/에 저장된다(editjob.py).
#   - 완전 자동(⚡): 링크를 넣으면 편집 없이 끝까지 만들어 비공개 업로드.
# ---------------------------------------------------------------------

STUDIO_URL = "https://studio.youtube.com/video/{video_id}/edit"


@app.get("/")
def index():
    return RedirectResponse("/auto")


@app.get("/auto", response_class=HTMLResponse)
def auto_view(request: Request):
    return templates.TemplateResponse(
        request, "auto.html",
        {
            "clip_length_presets": CLIP_LENGTH_PRESETS,
            "default_clip_length": DEFAULT_CLIP_LENGTH_PRESET,
            "programs": PROGRAMS,
            "moneyola": MONEYOLA,
        },
    )


@app.get("/auto/summary")
def auto_summary():
    """Everything the 자동 생성 page shows: per-source cards (shorts made
    today, new videos the scheduler noticed), the staged edit jobs, and the
    recent one-click auto jobs."""
    today = datetime.now(KST).date().isoformat()
    made_today: dict[str, int] = {}
    for it in library.list_items(WORK_DIR):
        created = datetime.fromisoformat(it["created_at"]).astimezone(KST).date().isoformat()
        if created == today:
            made_today[it["source_label"]] = made_today.get(it["source_label"], 0) + 1
    pending = editjob.pending_list(WORK_DIR)
    sources = [
        {
            "key": program.key,
            "name": program.name,
            "scheduled": program is not MONEYOLA,
            "made_today": made_today.get(program.name, 0),
            "pending": [p for p in pending if p["program_key"] == program.key],
        }
        for program in ALL_PROGRAMS
    ]
    recent_auto = [
        {"id": job_id, "program_name": job.get("program_name", ""), "video_title": job.get("video_title", ""),
         "status": job.get("status", ""), "message": job.get("message", "")}
        for job_id, job in reversed(list(AUTO_JOBS.items()))
    ][:8]
    return {"sources": sources, "edit_jobs": editjob.list_jobs(WORK_DIR), "recent_jobs": recent_auto}


# ------------------------------------------------------------- 단계별 작업

SOURCES: dict[str, YouTubeSource] = {}  # job_id -> source, so video info isn't re-fetched
RENDER_LOCK = asyncio.Semaphore(1)  # one confirmation render/upload at a time (CPU-heavy)


def _job_or_404(job_id: str) -> dict:
    job = editjob.load_job(WORK_DIR, job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


def _block_or_404(job: dict, index: int) -> dict:
    if not (0 <= index < len(job["blocks"])):
        raise HTTPException(404, "unknown block")
    return job["blocks"][index]


def _source_for(job: dict) -> YouTubeSource:
    source = SOURCES.get(job["id"])
    if source is None:
        source = SOURCES[job["id"]] = YouTubeSource(job["url"])
    return source


def _program_of(job: dict) -> ProgramConfig | None:
    try:
        return get_program(job["program_key"]) if job.get("program_key") else None
    except ValueError:
        return None


@app.post("/edit-jobs")
async def create_edit_job(
    program_key: str = Form(...),
    url: str = Form(""),
    clip_length: str = Form(DEFAULT_CLIP_LENGTH_PRESET),
):
    try:
        program = get_program(program_key.strip())
        get_clip_length_preset(clip_length)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    url = url.strip()
    if not url:
        # no link typed on a radio card = its newest video (the 새 영상 알림도 같은 영상)
        if not program.playlist_url:
            raise HTTPException(400, "영상 링크를 입력해 주세요.")
        video = await asyncio.get_event_loop().run_in_executor(
            None, scheduler.latest_playlist_video, program.playlist_url,
        )
        if video is None:
            raise HTTPException(502, f"{program.name}의 최신 영상을 찾지 못했습니다. 링크를 직접 넣어 주세요.")
        url = video.url
    job = editjob.create_job(
        WORK_DIR, program_key=program.key, program_name=program.name, url=url, clip_length_key=clip_length,
    )
    editjob.pending_remove(WORK_DIR, url)
    asyncio.create_task(_run_edit_analysis(job["id"]))
    return {"job_id": job["id"]}


async def _run_edit_analysis(job_id: str) -> None:
    def set_message(msg: str) -> None:
        try:
            editjob.update_job(WORK_DIR, job_id, lambda j: j.update(message=msg))
        except KeyError:
            pass  # job was deleted meanwhile

    job = editjob.load_job(WORK_DIR, job_id)
    if job is None:
        return
    try:
        result = await _prepare_source_and_candidates(
            editjob.job_dir(WORK_DIR, job_id), "url", job["url"], None, job["clip_length_key"],
            AUTO_DEFAULT_TIERS, "", on_status=set_message, program=_program_of(job),
            tier_counts=analyze.DEFAULT_TIER_COUNTS,
        )
        SOURCES[job_id] = result["source"]
        segments = result["segments"]
        blocks = editjob.build_blocks(
            result["candidates"], segments,
            guest_label=result["guest_label_guess"], shoot_date=result["shoot_date"],
            date_kind=result["date_kind"],
        )
        if not blocks:
            raise RuntimeError("조건에 맞는 쇼츠 후보를 찾지 못했습니다.")
        editjob.save_segments(WORK_DIR, job_id, segments)
        editjob.update_job(
            WORK_DIR, job_id,
            lambda j: j.update(status="editing", message="", video_title=result["video_title"], blocks=blocks),
        )
    except Exception as e:  # noqa: BLE001 - surface any failure to the page
        traceback.print_exc()
        try:
            editjob.update_job(WORK_DIR, job_id, lambda j: j.update(status="error", message=str(e)))
        except KeyError:
            pass


@app.get("/edit-jobs/{job_id}/view", response_class=HTMLResponse)
def edit_job_view(request: Request, job_id: str):
    _job_or_404(job_id)
    return templates.TemplateResponse(request, "edit.html", {"job_id": job_id})


def _block_view(job: dict, segments: list, block: dict, clip_max_sec: int) -> dict:
    """A block plus the derived facts the page needs."""
    view = dict(block)
    if segments:
        view["render_state"] = editjob.render_state(block, segments)
        view["duration"] = round(editjob.block_duration(block, segments), 1)
    else:
        view["render_state"] = block["render"]["status"]
        view["duration"] = 0
    view["render_version"] = block["render"].get("version", 0)
    view["studio_url"] = STUDIO_URL.format(video_id=block["upload"]["video_id"]) if block["upload"].get("video_id") else ""
    view["max_sec"] = clip_max_sec
    return view


def _job_view(job: dict, include_segments: bool) -> dict:
    segments = editjob.load_segments(WORK_DIR, job["id"]) if job["status"] != "analyzing" else []
    try:
        clip_max_sec = get_clip_length_preset(job["clip_length_key"]).max_sec
    except ValueError:
        clip_max_sec = 0
    out = {
        "id": job["id"], "program_name": job["program_name"], "video_title": job["video_title"],
        "url": job["url"], "status": job["status"], "message": job["message"],
        "stage": editjob.stage_label(job), "title_tag": (_program_of(job).title_tag if _program_of(job) else ""),
        "blocks": [_block_view(job, segments, b, clip_max_sec) for b in job["blocks"]],
        "context_lines": editjob.CONTEXT_LINES,
    }
    if include_segments:
        out["segments"] = [
            {"i": i, "start": s.start_sec, "end": s.end_sec, "text": s.text, "speaker": s.speaker}
            for i, s in enumerate(segments)
        ]
    return out


@app.get("/edit-jobs/{job_id}/data")
def edit_job_data(job_id: str):
    """Full state incl. the transcript - loaded once when the page opens."""
    return _job_view(_job_or_404(job_id), include_segments=True)


@app.get("/edit-jobs/{job_id}/status")
def edit_job_status(job_id: str):
    """Lightweight state for polling (no transcript)."""
    return _job_view(_job_or_404(job_id), include_segments=False)


@app.post("/edit-jobs/{job_id}/delete")
def edit_job_delete(job_id: str):
    _job_or_404(job_id)
    SOURCES.pop(job_id, None)
    editjob.delete_job(WORK_DIR, job_id)
    return {"ok": True}


async def _read_edit_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_edit(job_id: str, index: int, payload: dict) -> dict:
    """Validate + persist a block edit; returns the updated job."""
    job = _job_or_404(job_id)
    block = _block_or_404(job, index)
    if block["upload"]["status"] in ("uploading", "done"):
        raise HTTPException(409, "이미 업로드한 항목은 수정할 수 없습니다.")
    if block["render"]["status"] == "rendering":
        raise HTTPException(409, "확인 영상을 만드는 중에는 수정할 수 없습니다.")
    segments = editjob.load_segments(WORK_DIR, job_id)

    def mutate(j: dict) -> None:
        editjob.apply_block_edit(j["blocks"][index], segments, payload)
        editjob.mark_finished_if_done(j)

    try:
        return editjob.update_job(WORK_DIR, job_id, mutate)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/edit-jobs/{job_id}/blocks/{index}/save")
async def edit_block_save(request: Request, job_id: str, index: int):
    job = _apply_edit(job_id, index, await _read_edit_payload(request))
    segments = editjob.load_segments(WORK_DIR, job_id)
    max_sec = get_clip_length_preset(job["clip_length_key"]).max_sec
    return {"block": _block_view(job, segments, job["blocks"][index], max_sec)}


@app.post("/edit-jobs/{job_id}/blocks/{index}/render")
async def edit_block_render(request: Request, job_id: str, index: int):
    """Saves the posted edit (if any) and starts the confirmation render."""
    payload = await _read_edit_payload(request)
    job = _apply_edit(job_id, index, payload) if payload else _job_or_404(job_id)
    block = _block_or_404(job, index)
    if not block.get("enabled", True):
        raise HTTPException(400, "사용하지 않는 항목입니다.")
    if block["upload"]["status"] in ("uploading", "done"):
        raise HTTPException(409, "이미 업로드한 항목입니다.")
    if block["render"]["status"] == "rendering":
        return {"started": False}

    def mark(j: dict) -> None:
        j["blocks"][index]["render"].update(status="rendering", message="대기 중...")

    editjob.update_job(WORK_DIR, job_id, mark)
    asyncio.create_task(_run_block_render(job_id, index))
    return {"started": True}


def _set_render(job_id: str, index: int, **fields) -> None:
    try:
        editjob.update_job(WORK_DIR, job_id, lambda j: j["blocks"][index]["render"].update(fields))
    except KeyError:
        pass


async def _run_block_render(job_id: str, index: int) -> None:
    loop = asyncio.get_event_loop()
    async with RENDER_LOCK:
        try:
            job = editjob.load_job(WORK_DIR, job_id)
            if job is None:
                return
            block = job["blocks"][index]
            segments = editjob.load_segments(WORK_DIR, job_id)
            program = _program_of(job)
            program_key = program.key if program else None
            bdir = editjob.block_dir(WORK_DIR, job_id, index)
            bdir.mkdir(parents=True, exist_ok=True)
            new_render_hash = editjob.render_hash(block, segments)
            ranges, bounds = editjob.block_ranges(block, segments)
            wanted_clip = editjob.clip_hash(block, segments)
            clip_path = bdir / "clip.mp4"
            used_path = bdir / "clip_ranges.json"

            # footage is only re-cut when the kept ranges changed; caption/text
            # edits reuse the existing clip
            if block.get("clip_hash") == wanted_clip and clip_path.is_file() and used_path.is_file():
                used_ranges = [analyze.ClipRange(a, b) for a, b in json.loads(used_path.read_text(encoding="utf-8"))]
            else:
                _set_render(job_id, index, message="구간 추출/이어붙이는 중...")
                source = _source_for(job)
                parts_dir = bdir / "_parts"
                clip_path, used_ranges = await loop.run_in_executor(
                    None, clipbuild.build_clip_snapped, source, ranges, clip_path, parts_dir, bounds,
                )
                shutil.rmtree(parts_dir, ignore_errors=True)
                used_path.write_text(
                    json.dumps([[r.start_sec, r.end_sec] for r in used_ranges]), encoding="utf-8")
                editjob.update_job(WORK_DIR, job_id, lambda j: j["blocks"][index].update(clip_hash=wanted_clip))

            _set_render(job_id, index, message="자막 입히고 최종 렌더링 중...")
            cues = render.captions_for_ranges(editjob.block_cue_segments(block, segments), used_ranges)
            assets = render.RenderAssets(
                thumbnail_text=block["thumbnail_text"],
                guest_label=block["guest_label"],
                shoot_date=block["shoot_date"],
                date_kind=block["date_kind"],
                logo_top_left=_find_default_asset("logo_left", program_key),
                logo_top_right=(
                    _find_default_asset("logo_right", program_key)
                    if (program.show_top_right_logo if program else True) else None
                ),
                banner_bottom=_find_default_asset("banner_bottom", program_key),
            )
            await loop.run_in_executor(
                None,
                functools.partial(
                    render.render_final, clip_path, cues, assets, bdir / "final.mp4",
                    single_camera=program.single_camera if program else False,
                ),
            )
            version = block["render"].get("version", 0) + 1
            _set_render(job_id, index, status="ready", hash=new_render_hash, message="", version=version)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            _set_render(job_id, index, status="error", message=str(e))


@app.get("/edit-jobs/{job_id}/blocks/{index}/final.mp4")
def edit_block_video(job_id: str, index: int):
    job = _job_or_404(job_id)
    _block_or_404(job, index)
    path = editjob.block_dir(WORK_DIR, job_id, index) / "final.mp4"
    if not path.is_file():
        raise HTTPException(404, "확인 영상이 아직 없습니다.")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.post("/edit-jobs/{job_id}/blocks/{index}/upload")
async def edit_block_upload(job_id: str, index: int):
    job = _job_or_404(job_id)
    block = _block_or_404(job, index)
    segments = editjob.load_segments(WORK_DIR, job_id)
    if block["upload"]["status"] in ("uploading", "done"):
        raise HTTPException(409, "이미 업로드했거나 업로드 중입니다.")
    if editjob.render_state(block, segments) != "ready":
        raise HTTPException(409, "수정한 내용이 반영된 확인 영상을 먼저 만들어 주세요.")

    def mark(j: dict) -> None:
        j["blocks"][index]["upload"].update(status="uploading", message="유튜브에 비공개로 업로드 중...")

    editjob.update_job(WORK_DIR, job_id, mark)
    asyncio.create_task(_run_block_upload(job_id, index))
    return {"started": True}


def _set_upload(job_id: str, index: int, **fields) -> None:
    def mutate(j: dict) -> None:
        j["blocks"][index]["upload"].update(fields)
        editjob.mark_finished_if_done(j)
    try:
        editjob.update_job(WORK_DIR, job_id, mutate)
    except KeyError:
        pass


async def _run_block_upload(job_id: str, index: int) -> None:
    loop = asyncio.get_event_loop()
    try:
        job = editjob.load_job(WORK_DIR, job_id)
        block = job["blocks"][index]
        program = _program_of(job)
        final_path = editjob.block_dir(WORK_DIR, job_id, index) / "final.mp4"
        title = block["title"].strip() or block["thumbnail_text"].split("\n", 1)[0].strip() or "쇼츠"
        upload_title = f"[{program.title_tag}] {title}" if program and program.title_tag else title
        description = block["summary"]
        if block["shoot_date"]:
            description += f"\n[영상 {block['date_kind']} 날짜: {block['shoot_date']}]"
        result = await loop.run_in_executor(
            None, youtube_upload.upload_short, final_path, upload_title, description,
            YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE, "private",
        )
        if RADIHOLA_PLAYLIST_ID:
            try:
                await loop.run_in_executor(
                    None, youtube_upload.add_to_playlist, result.video_id,
                    RADIHOLA_PLAYLIST_ID, YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE,
                )
            except Exception:  # noqa: BLE001 - the upload itself already succeeded
                traceback.print_exc()
        library.add_item(WORK_DIR, library.new_item(
            source_label=job["program_name"] or job["video_title"], title=title, summary=block["summary"],
            thumbnail_text=block["thumbnail_text"], video_id=result.video_id, youtube_url=result.url,
        ))
        _set_upload(job_id, index, status="done", video_id=result.video_id, url=result.url, message="")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _set_upload(job_id, index, status="error", message=str(e))


async def _prepare_source_and_candidates(
    work_dir: Path,
    source_type: str,
    url: str,
    upload_path: Path | None,
    clip_length_key: str,
    tiers: list[str],
    custom_topic: str,
    on_status,
    program: ProgramConfig | None = None,
    tier_counts: dict[str, int] | None = None,
) -> dict:
    """Shared pipeline behind the staged flow (_run_edit_analysis) and the
    fully-automatic flow (_run_auto_pipeline): source setup, transcript,
    speaker labeling, guest-info extraction, and Claude candidate
    proposals. on_status(message) is called before each step so the caller
    can surface progress through whichever status dict it's tracking
    (an edit job or AUTO_JOBS[id]). Raises on any failure - callers decide how
    to record that as an error.
    """
    loop = asyncio.get_event_loop()

    # fail fast with a clear Korean message instead of burning several
    # minutes on transcript/whisper work only to hit a cryptic English SDK
    # error ("Could not resolve authentication method...") once the
    # pipeline finally reaches a Claude call - the most common cause by far
    # is a first-run .env with ANTHROPIC_API_KEY= left blank.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            f"ANTHROPIC_API_KEY가 설정되지 않았습니다. {REPO_ROOT / '.env'} 파일을 "
            "메모장으로 열어 ANTHROPIC_API_KEY= 뒤에 발급받은 키를 붙여넣고 저장한 "
            "뒤, 프로그램을 다시 실행해주세요."
        )

    on_status("영상 정보를 불러오는 중...")
    if source_type == "url":
        source = YouTubeSource(url)
    else:
        source = UploadedFileSource(upload_path)

    on_status("자막 추출 중 (없으면 음성인식, 몇 분 걸릴 수 있습니다)...")
    segments, transcript_source = await loop.run_in_executor(
        None, get_transcript_for_source, source, work_dir
    )

    on_status("화자(앵커/패널) 구분 중...")
    speakers = await loop.run_in_executor(None, analyze.label_speakers, segments)
    for seg, speaker in zip(segments, speakers):
        seg.speaker = speaker

    description = getattr(source, "description", None)
    guest_label_guess = ""
    if description:
        guest_label_guess = await loop.run_in_executor(
            None, analyze.extract_guest_info, source.title, description
        )

    # A 촬영 날짜 written in the source description (머니올라) is copied
    # as-is. Anything else (radio programs, or a description without one)
    # falls back to the video's publish date, labeled 방송 날짜.
    desc_date = render.extract_shoot_date_from_description(description)
    publish_date = render.format_shoot_date(getattr(source, "upload_date", None))
    is_radio = program is not None and not program.single_camera
    if desc_date and not is_radio:
        shoot_date, date_kind = desc_date, "촬영"
    else:
        shoot_date, date_kind = publish_date, "방송"
        if not shoot_date and is_radio:
            # the radio VOD is posted the day it airs, so when YouTube
            # didn't report a date, today's (KST) date is the best guess
            shoot_date = render.format_shoot_date(datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d"))
    if not shoot_date:
        print("[date] 영상에서 촬영/방송 날짜를 찾지 못해 날짜 표시를 생략합니다.")

    on_status("Claude로 쇼츠 후보 분석 중...")
    clip_length = get_clip_length_preset(clip_length_key)
    selected_tiers = [t for t in tiers if t in ("hook", "substantive")]
    candidates = await loop.run_in_executor(
        None,
        functools.partial(
            analyze.propose_candidates, segments, clip_length, selected_tiers,
            source.title, custom_topic or None, counts=tier_counts,
        ),
    )
    # 2nd pass: make each candidate start at a sentence start and end once
    # the speaker has finished (best-effort, keeps the original on failure)
    on_status("쇼츠 구간의 시작/끝을 문장 단위로 다듬는 중...")
    candidates = await loop.run_in_executor(
        None, analyze.refine_boundaries, candidates, segments, clip_length,
    )

    return {
        "source": source,
        "video_title": source.title,
        "segments": segments,
        "transcript_source": transcript_source,
        "guest_label_guess": guest_label_guess,
        "shoot_date": shoot_date,
        "date_kind": date_kind,
        "candidates": candidates,
    }


# ---------------------------------------------------------------------
# "완전 자동 모드" (⚡): a worker pastes a link and everything else -
# candidate selection (hook 3 + substantive 2), caption typo correction,
# rendering with a fixed department asset set, and a private YouTube upload
# - runs unattended, for when no editing is wanted. Needs a link: nothing
# starts on its own (the scheduler only notes new radio videos).
# ---------------------------------------------------------------------

AUTO_DEFAULT_TIERS = ["hook", "substantive"]
AUTO_COUNT = sum(analyze.DEFAULT_TIER_COUNTS.values())  # 5


@app.post("/auto-jobs")
async def create_auto_job(
    source_type: str = Form(...),
    url: str = Form(""),
    file: UploadFile | None = None,
    clip_length: str = Form(DEFAULT_CLIP_LENGTH_PRESET),
    program_key: str = Form(""),
):
    if source_type not in ("url", "upload"):
        raise HTTPException(400, "source_type must be 'url' or 'upload'")
    if source_type == "url" and not url.strip():
        raise HTTPException(400, "url is required")
    if source_type == "upload" and (file is None or not file.filename):
        raise HTTPException(400, "file is required")
    try:
        get_clip_length_preset(clip_length)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    program: ProgramConfig | None = None
    if program_key.strip():
        try:
            program = get_program(program_key.strip())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    auto_job_id = uuid.uuid4().hex[:12]
    work_dir = _auto_job_work_dir(auto_job_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    upload_path: Path | None = None
    if source_type == "upload":
        upload_path = work_dir / file.filename
        with upload_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

    AUTO_JOBS[auto_job_id] = {
        "status": "starting", "message": "", "video_title": "", "program_name": "", "items": [],
    }
    asyncio.create_task(
        _run_auto_pipeline(
            auto_job_id, source_type, url.strip(), upload_path, clip_length, AUTO_COUNT, program=program,
        )
    )
    return {"auto_job_id": auto_job_id}


async def _run_auto_pipeline(
    auto_job_id: str,
    source_type: str,
    url: str,
    upload_path: Path | None,
    clip_length_key: str,
    count: int,
    program: ProgramConfig | None = None,
) -> None:
    """Runs the full unattended pipeline (candidates -> per-candidate
    build/correct/render/upload) for one source, writing live progress into
    AUTO_JOBS[auto_job_id] and a record of each successful upload into the
    library store (see library.py - only used for the "오늘 N개" counts now).

    program is the card the worker used (라디올라 2개 / 머니올라) and decides
    the default_assets/ subfolder, logos and title tag; None falls back to
    the shared default_assets/ root images with no bottom banner.
    """
    auto_job = AUTO_JOBS[auto_job_id]
    work_dir = _auto_job_work_dir(auto_job_id)
    loop = asyncio.get_event_loop()
    source_label = program.name if program else ""
    program_key = program.key if program else None
    try:
        auto_job.update(status="running", message="")
        result = await _prepare_source_and_candidates(
            work_dir, source_type, url, upload_path, clip_length_key,
            AUTO_DEFAULT_TIERS, "",
            on_status=lambda msg: auto_job.update(message=msg),
            program=program, tier_counts=analyze.DEFAULT_TIER_COUNTS,
        )
        source = result["source"]
        segments = result["segments"]
        guest_label_guess = result["guest_label_guess"]
        shoot_date = result["shoot_date"]
        date_kind = result["date_kind"]
        candidates = result["candidates"]
        auto_job["video_title"] = result["video_title"]
        if not source_label:
            source_label = result["video_title"]

        if not candidates:
            raise RuntimeError("조건에 맞는 쇼츠 후보를 찾지 못했습니다.")

        selected = candidates[:count]
        auto_job["items"] = [
            {"status": "pending", "title": c.title, "message": "", "youtube_url": None}
            for c in selected
        ]
        auto_job.update(status="processing", message=f"{len(selected)}개 항목 자동 처리 중...")

        for i, candidate in enumerate(selected):
            item = auto_job["items"][i]
            item_dir = work_dir / f"item_{i}"
            item_dir.mkdir(parents=True, exist_ok=True)
            try:
                item.update(status="building", message="구간 추출/이어붙이는 중...")
                # cuts are moved into the nearest audio pauses, so captions
                # must follow the ranges actually used, not the requested ones
                clip_path, used_ranges = await loop.run_in_executor(
                    None, clipbuild.build_clip_snapped, source, candidate.ranges, item_dir / "clip.mp4"
                )

                item.update(message="자막 맞춤법 교정 중...")
                cues = render.captions_for_ranges(segments, used_ranges)
                corrected_texts = await loop.run_in_executor(
                    None, analyze.correct_caption_errors, [c[2] for c in cues]
                )
                cues = [(s, e, t) for (s, e, _old), t in zip(cues, corrected_texts)]

                item.update(status="rendering", message="최종 렌더링 중...")
                show_top_right_logo = program.show_top_right_logo if program else True
                single_camera = program.single_camera if program else False
                assets = render.RenderAssets(
                    thumbnail_text=candidate.thumbnail_text,
                    guest_label=guest_label_guess,
                    shoot_date=shoot_date,
                    date_kind=date_kind,
                    logo_top_left=_find_default_asset("logo_left", program_key),
                    logo_top_right=(
                        _find_default_asset("logo_right", program_key) if show_top_right_logo else None
                    ),
                    banner_bottom=_find_default_asset("banner_bottom", program_key),
                )
                final_path = await loop.run_in_executor(
                    None,
                    functools.partial(
                        render.render_final, clip_path, cues, assets, item_dir / "final.mp4",
                        single_camera=single_camera,
                    ),
                )

                item.update(status="uploading", message="유튜브에 비공개로 업로드 중...")
                title_line1 = candidate.thumbnail_text.split("\n", 1)[0].strip() or candidate.title
                description = candidate.summary
                if shoot_date:
                    description += f"\n[영상 {date_kind} 날짜: {shoot_date}]"
                # 프로그램별 구분 표시를 제목 앞에 붙임 (주간 보고서 분류용, config.py 참고)
                upload_title = (
                    f"[{program.title_tag}] {title_line1}" if program and program.title_tag else title_line1
                )
                upload_result = await loop.run_in_executor(
                    None, youtube_upload.upload_short, final_path, upload_title, description,
                    YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE, "private",
                )

                if RADIHOLA_PLAYLIST_ID:
                    try:
                        await loop.run_in_executor(
                            None, youtube_upload.add_to_playlist, upload_result.video_id,
                            RADIHOLA_PLAYLIST_ID, YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE,
                        )
                    except Exception:  # noqa: BLE001 - the upload itself already succeeded; don't fail the item over this
                        traceback.print_exc()

                library.add_item(WORK_DIR, library.new_item(
                    source_label=source_label, title=title_line1, summary=candidate.summary,
                    thumbnail_text=candidate.thumbnail_text, video_id=upload_result.video_id,
                    youtube_url=upload_result.url,
                ))
                item.update(status="done", message="완료", youtube_url=upload_result.url)
            except Exception as e:  # noqa: BLE001 - one item failing shouldn't stop the rest
                traceback.print_exc()
                item.update(status="error", message=str(e))

        failed = sum(1 for it in auto_job["items"] if it["status"] == "error")
        succeeded = len(auto_job["items"]) - failed
        auto_job.update(
            status="done",
            message=f"완료: {succeeded}개 업로드 성공" + (f", {failed}개 실패" if failed else ""),
        )
    except Exception as e:  # noqa: BLE001 - setup-level failure, before any items ran
        traceback.print_exc()
        auto_job.update(status="error", message=str(e))


@app.get("/auto-jobs/{auto_job_id}")
def auto_job_status(auto_job_id: str):
    auto_job = AUTO_JOBS.get(auto_job_id)
    if auto_job is None:
        raise HTTPException(404, "unknown auto job")
    return {
        "status": auto_job["status"],
        "message": auto_job["message"],
        "video_title": auto_job.get("video_title", ""),
        "program_name": auto_job.get("program_name", ""),
        "items": auto_job.get("items", []),
    }


@app.get("/auto-jobs/{auto_job_id}/view", response_class=HTMLResponse)
def auto_job_view(request: Request, auto_job_id: str):
    if auto_job_id not in AUTO_JOBS:
        raise HTTPException(404, "unknown auto job")
    return templates.TemplateResponse(request, "auto_status.html", {"auto_job_id": auto_job_id})


# ---------------------------------------------------------------------
# Scheduler: watches config.PROGRAMS's playlists (see scheduler.py) and, when
# a new video of today shows up, only NOTES it (work/pending_videos.json) so
# the 자동 생성 page can offer "✂️ 편집 시작". Nothing is analyzed or
# generated on its own, so no tokens are spent on videos nobody asked for.
# Started as a background task at app startup, below.
# ---------------------------------------------------------------------

async def _on_scheduler_new_video(program: ProgramConfig, video: scheduler.PlaylistVideo) -> None:
    print(f"[scheduler] {program.name}: 새 영상 알림 - {video.title} ({video.url})")
    editjob.pending_add(WORK_DIR, program.key, video.id, video.url, video.title)


@app.on_event("startup")
async def _start_scheduler() -> None:
    # tidy up first: jobs left half-done by a previous run can't continue,
    # and finished jobs' big intermediate videos are dropped after a week
    editjob.recover_interrupted(WORK_DIR)
    editjob.cleanup_old(WORK_DIR)
    editjob.cleanup_old_dirs(WORK_DIR / "auto_jobs")
    # escape hatch for tests/dev tooling that spins up this app without
    # wanting a forever-running background task making real network calls
    # against the real PROGRAMS playlists; defaults on for actual use.
    if os.environ.get("AUTOSHORTS_SCHEDULER_ENABLED", "1") == "0":
        return
    asyncio.create_task(scheduler.run_scheduler_loop(WORK_DIR, _on_scheduler_new_video, PROGRAMS))

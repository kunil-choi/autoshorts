"""Local review web app for autoshorts.

Run with: uvicorn webui.server:app --reload --port 8787

Runs on the worker's own machine (not a shared server) so downloads and
rendering happen locally - see the project README for why. Two job
concepts:

- JOBS: one per input (a URL or an uploaded file) - covers steps 1-4
  (source setup, transcript, Claude candidate analysis).
- BUILDS: one per shorts clip a worker is assembling out of a job's
  transcript - covers steps 6-9 (hard-cut clip build, draft render,
  caption correction, final render). A single job can spawn several
  builds (a worker cutting more than one short from the same source).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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

from autoshorts import analyze, clipbuild, library, render, scheduler, youtube_upload  # noqa: E402
from autoshorts.config import (  # noqa: E402
    CLIP_LENGTH_PRESETS, DEFAULT_CLIP_LENGTH_PRESET, PROGRAMS, ProgramConfig, get_clip_length_preset, get_program,
)
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

JOBS: dict[str, dict] = {}
BUILDS: dict[str, dict] = {}
# "완전 자동 모드" (see create_auto_job) - a separate path from JOBS/BUILDS
# that skips the review screen entirely: source -> candidates -> (build,
# correct captions, render, upload) per candidate, all unattended.
AUTO_JOBS: dict[str, dict] = {}


def _job_work_dir(job_id: str) -> Path:
    return WORK_DIR / "jobs" / job_id


def _build_work_dir(job_id: str, build_id: str) -> Path:
    return WORK_DIR / "jobs" / job_id / "builds" / build_id


def _auto_job_work_dir(auto_job_id: str) -> Path:
    return WORK_DIR / "auto_jobs" / auto_job_id


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {
            "clip_length_presets": CLIP_LENGTH_PRESETS,
            "default_clip_length": DEFAULT_CLIP_LENGTH_PRESET,
            "programs": PROGRAMS,
        },
    )


@app.post("/jobs")
async def create_job(
    source_type: str = Form(...),
    url: str = Form(""),
    file: UploadFile | None = None,
    clip_length: str = Form(DEFAULT_CLIP_LENGTH_PRESET),
    tiers: list[str] = Form([]),
    custom_topic: str = Form(""),
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

    job_id = uuid.uuid4().hex[:12]
    work_dir = _job_work_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)

    upload_path: Path | None = None
    if source_type == "upload":
        upload_path = work_dir / file.filename
        with upload_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

    JOBS[job_id] = {
        "status": "starting",
        "message": "",
        "source_type": source_type,
        "clip_length": clip_length,
    }
    asyncio.create_task(
        _run_job(job_id, source_type, url.strip(), upload_path, clip_length, tiers, custom_topic.strip())
    )
    return {"job_id": job_id}


async def _prepare_source_and_candidates(
    work_dir: Path,
    source_type: str,
    url: str,
    upload_path: Path | None,
    clip_length_key: str,
    tiers: list[str],
    custom_topic: str,
    on_status,
) -> dict:
    """Shared pipeline behind both the review-and-confirm flow (_run_job)
    and the fully-automatic flow (_run_auto_job): source setup, transcript,
    speaker labeling, guest-info extraction, and Claude candidate
    proposals. on_status(message) is called before each step so the caller
    can surface progress through whichever status dict it's tracking
    (JOBS[id] or AUTO_JOBS[id]). Raises on any failure - callers decide how
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

    on_status("Claude로 쇼츠 후보 분석 중...")
    clip_length = get_clip_length_preset(clip_length_key)
    selected_tiers = [t for t in tiers if t in ("hook", "substantive")]
    candidates = await loop.run_in_executor(
        None, analyze.propose_candidates, segments, clip_length, selected_tiers,
        source.title, custom_topic or None,
    )

    return {
        "source": source,
        "video_title": source.title,
        "segments": segments,
        "transcript_source": transcript_source,
        "guest_label_guess": guest_label_guess,
        "candidates": candidates,
    }


async def _run_job(
    job_id: str,
    source_type: str,
    url: str,
    upload_path: Path | None,
    clip_length_key: str,
    tiers: list[str],
    custom_topic: str,
) -> None:
    job = JOBS[job_id]
    work_dir = _job_work_dir(job_id)
    try:
        job.update(status="running", message="")
        result = await _prepare_source_and_candidates(
            work_dir, source_type, url, upload_path, clip_length_key, tiers, custom_topic,
            on_status=lambda msg: job.update(message=msg),
        )
        job["source"] = result["source"]
        job["video_title"] = result["video_title"]
        job["segments"] = result["segments"]
        job["transcript_source"] = result["transcript_source"]
        job["guest_label_guess"] = result["guest_label_guess"]
        job["candidates"] = [analyze.candidate_to_dict(c) for c in result["candidates"]]
        job.update(status="ready", message="완료")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        traceback.print_exc()  # full trace goes to the console window, not just the short UI message
        job.update(status="error", message=str(e))


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return {"status": job["status"], "message": job["message"]}


@app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
def review(request: Request, job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    if job["status"] != "ready":
        raise HTTPException(409, f"job is not ready yet (status={job['status']})")

    source = job["source"]
    is_youtube = isinstance(source, YouTubeSource)
    segments = [
        {"start_sec": s.start_sec, "end_sec": s.end_sec, "text": s.text, "speaker": s.speaker}
        for s in job["segments"]
    ]
    return templates.TemplateResponse(
        request, "review.html",
        {
            "job_id": job_id,
            "video_title": job["video_title"],
            "is_youtube": is_youtube,
            "youtube_id": source.id if is_youtube else None,
            "segments_json": json.dumps(segments, ensure_ascii=False),
            "candidates": job["candidates"],
            "candidates_json": json.dumps(job["candidates"], ensure_ascii=False),
            "guest_label_guess": job.get("guest_label_guess", ""),
        },
    )


@app.get("/jobs/{job_id}/source-media")
def source_media(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    source = job.get("source")
    if not isinstance(source, UploadedFileSource):
        raise HTTPException(404, "this job has no local media file")
    return FileResponse(source.path)


@app.post("/jobs/{job_id}/builds")
async def create_build(job_id: str, ranges: str = Form(...)):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    try:
        raw_ranges = json.loads(ranges)
        clip_ranges = [analyze.ClipRange(float(r[0]), float(r[1])) for r in raw_ranges]
    except (json.JSONDecodeError, TypeError, ValueError, IndexError) as e:
        raise HTTPException(400, f"invalid ranges: {e}") from e
    if not clip_ranges:
        raise HTTPException(400, "at least one range is required")

    build_id = uuid.uuid4().hex[:12]
    BUILDS[build_id] = {"status": "starting", "message": "", "job_id": job_id, "ranges": raw_ranges}
    asyncio.create_task(_run_build_draft(job_id, build_id, clip_ranges))
    return {"build_id": build_id}


async def _run_build_draft(job_id: str, build_id: str, ranges: list) -> None:
    job = JOBS[job_id]
    build = BUILDS[build_id]
    work_dir = _build_work_dir(job_id, build_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_event_loop()
    try:
        build.update(status="running", message="구간 추출/이어붙이는 중...")
        clip_path = await loop.run_in_executor(
            None, clipbuild.build_clip, job["source"], ranges, work_dir / "clip.mp4"
        )
        build["clip_path"] = clip_path
        build["clip_ranges"] = ranges

        cues = render.captions_for_ranges(job["segments"], ranges)
        build["cues"] = cues

        build.update(message="초안 렌더링 중...")
        draft_path = await loop.run_in_executor(
            None, render.render_draft, clip_path, cues, work_dir / "draft.mp4"
        )
        build["draft_path"] = draft_path
        build.update(status="draft_ready", message="초안 완료")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        build.update(status="error", message=str(e))


@app.get("/builds/{build_id}")
def build_status(build_id: str):
    build = BUILDS.get(build_id)
    if build is None:
        raise HTTPException(404, "unknown build")
    resp = {"status": build["status"], "message": build["message"]}
    if build.get("cues") is not None:
        resp["cues"] = build["cues"]
    return JSONResponse(resp)


@app.get("/builds/{build_id}/draft.mp4")
def build_draft_video(build_id: str):
    build = BUILDS.get(build_id)
    if build is None or not build.get("draft_path"):
        raise HTTPException(404, "draft not ready")
    return FileResponse(build["draft_path"])


@app.post("/builds/{build_id}/final")
async def create_final(
    build_id: str,
    captions: str = Form(...),
    thumbnail_text: str = Form(""),
    guest_label: str = Form(""),
    logo_top_left: UploadFile | None = None,
    logo_top_right: UploadFile | None = None,
    banner_bottom: UploadFile | None = None,
):
    build = BUILDS.get(build_id)
    if build is None:
        raise HTTPException(404, "unknown build")
    if not build.get("clip_path"):
        raise HTTPException(409, "draft must be built before finalizing")
    try:
        cues = [(float(c[0]), float(c[1]), str(c[2])) for c in json.loads(captions)]
    except (json.JSONDecodeError, TypeError, ValueError, IndexError) as e:
        raise HTTPException(400, f"invalid captions: {e}") from e

    work_dir = Path(build["clip_path"]).parent
    assets_dir = work_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    # a worker can re-finalize the same build after tweaking captions or
    # metadata without re-picking logo/banner images every time (the
    # browser's file input does carry the previous selection forward on its
    # own, but that shouldn't be the only thing this relies on) - fall back
    # to whatever was used on this build's last final render when no new
    # file is attached this time, instead of silently dropping it.
    prev_assets: render.RenderAssets | None = build.get("assets")

    def _save(upload: UploadFile | None, name: str, previous: Path | None) -> Path | None:
        if upload is None or not upload.filename:
            return previous
        dest = assets_dir / f"{name}{Path(upload.filename).suffix}"
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        return dest

    assets = render.RenderAssets(
        thumbnail_text=thumbnail_text,
        guest_label=guest_label,
        logo_top_left=_save(logo_top_left, "logo_left", prev_assets.logo_top_left if prev_assets else None),
        logo_top_right=_save(logo_top_right, "logo_right", prev_assets.logo_top_right if prev_assets else None),
        banner_bottom=_save(banner_bottom, "banner_bottom", prev_assets.banner_bottom if prev_assets else None),
    )
    build["assets"] = assets
    build["status"] = "finalizing"
    build["message"] = "최종 렌더링 중..."
    asyncio.create_task(_run_build_final(build_id, cues, assets))
    return {"build_id": build_id}


async def _run_build_final(build_id: str, cues: list, assets: render.RenderAssets) -> None:
    build = BUILDS[build_id]
    work_dir = Path(build["clip_path"]).parent
    loop = asyncio.get_event_loop()
    try:
        final_path = await loop.run_in_executor(
            None, render.render_final, Path(build["clip_path"]), cues, assets, work_dir / "final.mp4"
        )
        build["final_path"] = final_path
        build.update(status="done", message="완료")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        build.update(status="error", message=str(e))


@app.get("/builds/{build_id}/final.mp4")
def build_final_video(build_id: str):
    build = BUILDS.get(build_id)
    if build is None or not build.get("final_path"):
        raise HTTPException(404, "final render not ready")
    return FileResponse(build["final_path"], filename="short.mp4")


# ---------------------------------------------------------------------
# "완전 자동 모드": a worker picks only a source + clip length + how many
# shorts to produce, and everything else - candidate selection, caption
# typo correction, rendering with a fixed department asset set, and a
# private YouTube upload - runs unattended. Kept entirely separate from
# JOBS/BUILDS/review.html so the existing recommend-and-confirm flow is
# untouched; a worker who wants manual control over segment trimming,
# caption edits, or per-clip images still uses that path instead.
# ---------------------------------------------------------------------

AUTO_DEFAULT_TIERS = ["hook", "substantive"]
AUTO_MIN_COUNT = 1
AUTO_MAX_COUNT = 10


@app.post("/auto-jobs")
async def create_auto_job(
    source_type: str = Form(...),
    url: str = Form(""),
    file: UploadFile | None = None,
    clip_length: str = Form(DEFAULT_CLIP_LENGTH_PRESET),
    count: int = Form(3),
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
    count = max(AUTO_MIN_COUNT, min(count, AUTO_MAX_COUNT))

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
            auto_job_id, source_type, url.strip(), upload_path, clip_length, count, program=program,
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
    AUTO_JOBS[auto_job_id] and a permanent record of each successful upload
    into the library store (see library.py / the /library screen).

    Shared by two callers: the manual "완전 자동 모드" button (create_auto_job
    above - program is the one the worker picked in the form, or None if they
    left it on "선택 안함", which falls back to the shared default_assets/
    root images with no bottom banner) and the scheduler (program is always
    set, from PROGRAMS - uses that program's own default_assets/ subfolder
    and label, see _on_scheduler_new_video below).
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
        )
        source = result["source"]
        segments = result["segments"]
        guest_label_guess = result["guest_label_guess"]
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
                clip_path = await loop.run_in_executor(
                    None, clipbuild.build_clip, source, candidate.ranges, item_dir / "clip.mp4"
                )

                item.update(message="자막 맞춤법 교정 중...")
                cues = render.captions_for_ranges(segments, candidate.ranges)
                corrected_texts = await loop.run_in_executor(
                    None, analyze.correct_caption_errors, [c[2] for c in cues]
                )
                cues = [(s, e, t) for (s, e, _old), t in zip(cues, corrected_texts)]

                item.update(status="rendering", message="최종 렌더링 중...")
                show_top_right_logo = program.show_top_right_logo if program else True
                assets = render.RenderAssets(
                    thumbnail_text=candidate.thumbnail_text,
                    guest_label=guest_label_guess,
                    logo_top_left=_find_default_asset("logo_left", program_key),
                    logo_top_right=(
                        _find_default_asset("logo_right", program_key) if show_top_right_logo else None
                    ),
                    banner_bottom=_find_default_asset("banner_bottom", program_key),
                )
                final_path = await loop.run_in_executor(
                    None, render.render_final, clip_path, cues, assets, item_dir / "final.mp4"
                )

                item.update(status="uploading", message="유튜브에 비공개로 업로드 중...")
                title_line1 = candidate.thumbnail_text.split("\n", 1)[0].strip() or candidate.title
                upload_result = await loop.run_in_executor(
                    None, youtube_upload.upload_short, final_path, title_line1, candidate.summary,
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
# Scheduler: watches config.PROGRAMS's playlists and runs _run_auto_pipeline
# on its own once each weekday's VOD shows up - see scheduler.py. Started
# as a background task at app startup, below.
# ---------------------------------------------------------------------

async def _on_scheduler_new_video(program: ProgramConfig, video: scheduler.PlaylistVideo) -> None:
    auto_job_id = uuid.uuid4().hex[:12]
    work_dir = _auto_job_work_dir(auto_job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    AUTO_JOBS[auto_job_id] = {
        "status": "starting", "message": "", "video_title": video.title,
        "program_name": program.name, "items": [],
    }
    print(f"[scheduler] {program.name}: 자동 작업 시작 (auto_job_id={auto_job_id}, {video.url})")
    await _run_auto_pipeline(
        auto_job_id, "url", video.url, None, program.clip_length_key, program.count, program=program,
    )


@app.on_event("startup")
async def _start_scheduler() -> None:
    # escape hatch for tests/dev tooling that spins up this app without
    # wanting a forever-running background task making real network calls
    # against the real PROGRAMS playlists; defaults on for actual use.
    if os.environ.get("AUTOSHORTS_SCHEDULER_ENABLED", "1") == "0":
        return
    asyncio.create_task(scheduler.run_scheduler_loop(WORK_DIR, _on_scheduler_new_video, PROGRAMS))


# ---------------------------------------------------------------------
# /library: every short the automatic pipeline (manual button or
# scheduler) has uploaded, so a worker can publish or delete each one
# in-app instead of going to YouTube Studio - see library.py.
# ---------------------------------------------------------------------

@app.get("/library", response_class=HTMLResponse)
def library_view(request: Request):
    return templates.TemplateResponse(request, "library.html", {})


@app.get("/library/items")
def library_items():
    return {"items": library.list_items(WORK_DIR)}


@app.post("/library/items/{item_id}/publish")
async def library_publish(item_id: str):
    item = library.get_item(WORK_DIR, item_id)
    if item is None:
        raise HTTPException(404, "unknown library item")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, youtube_upload.set_privacy, item["video_id"], "public",
            YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE,
        )
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(502, f"공개 전환 실패: {e}") from e
    updated = library.update_status(WORK_DIR, item_id, "public")
    return {"item": updated}


@app.post("/library/items/{item_id}/delete")
async def library_delete(item_id: str):
    item = library.get_item(WORK_DIR, item_id)
    if item is None:
        raise HTTPException(404, "unknown library item")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, youtube_upload.delete_video, item["video_id"],
            YOUTUBE_CLIENT_SECRET_FILE, YOUTUBE_TOKEN_FILE,
        )
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(502, f"삭제 실패: {e}") from e
    updated = library.update_status(WORK_DIR, item_id, "deleted")
    return {"item": updated}

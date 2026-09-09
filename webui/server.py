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
import shutil
import sys
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

from autoshorts import analyze, clipbuild, render  # noqa: E402
from autoshorts.config import CLIP_LENGTH_PRESETS, DEFAULT_CLIP_LENGTH_PRESET, get_clip_length_preset  # noqa: E402
from autoshorts.sources import UploadedFileSource, YouTubeSource  # noqa: E402
from autoshorts.transcript import get_transcript_for_source  # noqa: E402

WORK_DIR = REPO_ROOT / "work"
WORK_DIR.mkdir(exist_ok=True)

app = FastAPI(title="autoshorts")
templates = Jinja2Templates(directory=str(WEBUI_DIR / "templates"))
app.mount(
    "/static", StaticFiles(directory=str(WEBUI_DIR / "static")), name="static"
)

JOBS: dict[str, dict] = {}
BUILDS: dict[str, dict] = {}


def _job_work_dir(job_id: str) -> Path:
    return WORK_DIR / "jobs" / job_id


def _build_work_dir(job_id: str, build_id: str) -> Path:
    return WORK_DIR / "jobs" / job_id / "builds" / build_id


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {"clip_length_presets": CLIP_LENGTH_PRESETS, "default_clip_length": DEFAULT_CLIP_LENGTH_PRESET},
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
    loop = asyncio.get_event_loop()
    try:
        job.update(status="running", message="영상 정보를 불러오는 중...")
        if source_type == "url":
            source = YouTubeSource(url)
        else:
            source = UploadedFileSource(upload_path)
        job["source"] = source

        job.update(message="자막 추출 중 (없으면 음성인식, 몇 분 걸릴 수 있습니다)...")
        segments, transcript_source = await loop.run_in_executor(
            None, get_transcript_for_source, source, work_dir
        )
        job["segments"] = segments
        job["transcript_source"] = transcript_source
        job["video_title"] = source.title

        description = getattr(source, "description", None)
        guest_label_guess = ""
        if description:
            guest_label_guess = await loop.run_in_executor(
                None, analyze.extract_guest_info, source.title, description
            )
        job["guest_label_guess"] = guest_label_guess

        job.update(message="Claude로 쇼츠 후보 분석 중...")
        clip_length = get_clip_length_preset(clip_length_key)
        selected_tiers = [t for t in tiers if t in ("hook", "substantive")]
        candidates = await loop.run_in_executor(
            None, analyze.propose_candidates, segments, clip_length, selected_tiers,
            source.title, custom_topic or None,
        )
        job["candidates"] = [analyze.candidate_to_dict(c) for c in candidates]

        job.update(status="ready", message="완료")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
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
        {"start_sec": s.start_sec, "end_sec": s.end_sec, "text": s.text}
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

    def _save(upload: UploadFile | None, name: str) -> Path | None:
        if upload is None or not upload.filename:
            return None
        dest = assets_dir / f"{name}{Path(upload.filename).suffix}"
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)
        return dest

    assets = render.RenderAssets(
        thumbnail_text=thumbnail_text,
        guest_label=guest_label,
        logo_top_left=_save(logo_top_left, "logo_left"),
        logo_top_right=_save(logo_top_right, "logo_right"),
        banner_bottom=_save(banner_bottom, "banner_bottom"),
    )
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
        build.update(status="error", message=str(e))


@app.get("/builds/{build_id}/final.mp4")
def build_final_video(build_id: str):
    build = BUILDS.get(build_id)
    if build is None or not build.get("final_path"):
        raise HTTPException(404, "final render not ready")
    return FileResponse(build["final_path"], filename="short.mp4")

"""Local review web app for autoshorts.

Run with: uvicorn webui.server:app --reload --port 8787

Runs on the worker's own machine (not a shared server) so downloads and
rendering happen locally - see the project README for why. Routes below
are stubs matching the 9-step flow; each fills in as the corresponding
backend piece (sources, analyze, clipbuild, render) is implemented.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv(Path(__file__).resolve().parent / ".env")

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

app = FastAPI(title="autoshorts")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static"
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# step 1-2: input (URL or uploaded file) -> transcript + tier analysis
@app.post("/jobs")
async def create_job(url: str = Form(None)):
    raise HTTPException(501, "not implemented yet")


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    raise HTTPException(501, "not implemented yet")


# step 5-6: the review screen (video + transcript timeline, multi-select)
@app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
def review(request: Request, job_id: str):
    raise HTTPException(501, "not implemented yet")


# step 7: draft render (no banner/logos) for caption proofreading
@app.post("/jobs/{job_id}/draft")
async def start_draft_render(job_id: str):
    raise HTTPException(501, "not implemented yet")


# step 8-9: metadata + final render
@app.post("/jobs/{job_id}/final")
async def start_final_render(job_id: str):
    raise HTTPException(501, "not implemented yet")

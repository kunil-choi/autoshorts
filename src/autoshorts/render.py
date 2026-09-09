"""Composite a built clip (clipbuild.py) into the final vertical (9:16)
shorts look - ported and generalized from radihola's render.py (crop,
title banner, bottom info band, burned-in captions).

Two-stage, per the review flow (steps 7-9):
  - render_draft(): the hard-cut clip + burned-in captions only, no
    banner/logos - fast, so the worker can proofread captions against
    picture before anything else is finalized.
  - render_final(): draft's captions (worker-corrected) plus the title
    banner, guest name plate, and the logo images uploaded in step 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .transcript import Segment


@dataclass
class RenderAssets:
    thumbnail_text: str = ""
    guest_label: str = ""
    logo_top_left: Path | None = None
    logo_top_right: Path | None = None
    banner_bottom: Path | None = None


def render_draft(clip_path: Path, captions: list[Segment], out_path: Path) -> Path:
    raise NotImplementedError


def render_final(clip_path: Path, captions: list[Segment], assets: RenderAssets, out_path: Path) -> Path:
    raise NotImplementedError

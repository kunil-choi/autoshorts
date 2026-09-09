"""Ask Claude to propose shorts candidates from a timestamped transcript.

Two tiers run by default - "hook" (virality-first) and "substantive"
(content-first), matching radihola's original CANDIDATE_TIERS - plus an
optional third "custom" tier, only run when the worker types a topic in
step 3 of the review flow. Each candidate now covers a *list* of
[start_sec, end_sec] ranges (see clipbuild.py), not a single range, so a
tier can propose a candidate stitched from multiple non-adjacent moments.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ClipLengthPreset
from .transcript import Segment

TIER_LABELS = {
    "hook": "훅 위주",
    "substantive": "핵심 내용 위주",
    "custom": "직접 입력한 주제",
}


@dataclass
class ClipRange:
    start_sec: float
    end_sec: float


@dataclass
class Candidate:
    ranges: list[ClipRange]
    title: str
    summary: str
    thumbnail_text: str
    reason: str
    tier: str = ""


def propose_candidates(
    segments: list[Segment],
    clip_length: ClipLengthPreset,
    tiers: list[str],
    custom_topic: str | None = None,
) -> list[Candidate]:
    """tiers is a subset of ("hook", "substantive"); pass custom_topic to
    also run the "custom" tier against that topic. Implementation (the
    actual Claude tool-use calls, ported and generalized from radihola's
    analyze.py) lands with the data-model work."""
    raise NotImplementedError


def correct_caption_errors(texts: list[str]) -> list[str]:
    """Best-effort pass to fix likely speech-recognition mishearings in a
    batch of caption lines - see radihola's analyze.py for the reference
    implementation this will be ported from."""
    raise NotImplementedError


def extract_guest_info(video_title: str, description: str | None) -> str:
    """Best-effort "이름 직책 / 소속" guest label extracted from title+description,
    used to prefill (never force) the guest-name field in step 8."""
    raise NotImplementedError

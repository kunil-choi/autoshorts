"""MediaSource backed by a YouTube URL/video id, via yt-dlp.

Runs locally (not on a shared server) so YouTube sees the worker's own
network IP instead of a cloud IP - see the parent project's README for why
that matters. Implementation will be ported over from radihola's
youtube.py (download_audio / download_auto_captions / download_segment),
adapted to this class shape.
"""

from __future__ import annotations

from pathlib import Path

from .base import MediaSource


class YouTubeSource(MediaSource):
    def __init__(self, url_or_id: str):
        self.id = self._extract_video_id(url_or_id)
        self.title = ""  # populated by _fetch_info() once implemented

    @staticmethod
    def _extract_video_id(url_or_id: str) -> str:
        raise NotImplementedError

    def duration_sec(self) -> float:
        raise NotImplementedError

    def captions_vtt(self, lang: str = "ko") -> Path | None:
        raise NotImplementedError

    def extract_audio(self, out_dir: Path) -> Path:
        raise NotImplementedError

    def extract_clip(self, start_sec: float, end_sec: float, out_path: Path) -> Path:
        raise NotImplementedError

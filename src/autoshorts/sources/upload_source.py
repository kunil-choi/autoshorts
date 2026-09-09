"""MediaSource backed by a video file the worker already has on their own
machine - no download step, so no YouTube bot-block exposure, and no
generation loss from YouTube's own upload transcoding (see the project
README's quality-vs-URL-mode note).
"""

from __future__ import annotations

from pathlib import Path

from .base import MediaSource


class UploadedFileSource(MediaSource):
    def __init__(self, file_path: Path, title: str | None = None):
        self.path = Path(file_path)
        self.id = self.path.stem
        self.title = title or self.path.stem

    def duration_sec(self) -> float:
        raise NotImplementedError

    def captions_vtt(self, lang: str = "ko") -> Path | None:
        return None

    def extract_audio(self, out_dir: Path) -> Path:
        raise NotImplementedError

    def extract_clip(self, start_sec: float, end_sec: float, out_path: Path) -> Path:
        raise NotImplementedError

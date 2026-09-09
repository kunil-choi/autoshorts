"""Composite a built clip (clipbuild.py) into the final vertical (9:16)
shorts look - ported and generalized from radihola's render.py.

Differences from radihola's version:
  - operates on an already-built clip file (clipbuild.build_clip's output),
    not a freshly-downloaded YouTube segment - no pad_sec/offset dance is
    needed since MediaSource.extract_clip already returns exact ranges.
  - caption timing is computed across possibly several concatenated ranges
    (see _captions_for_ranges), not a single [start_sec, end_sec] span.
  - logos and the bottom band image are always worker-uploaded at render
    time (step 8 of the review flow), never bundled defaults - so there is
    no text-fallback for them, only "show the uploaded image, or nothing".
  - split into two stages, matching the review flow: render_draft() (crop +
    captions only, plain center crop for speed) so the worker can proofread
    captions before anything is finalized, and render_final() (adds the
    face-detected crop, title banner, logos, guest label, bottom image).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .analyze import ClipRange
from .transcript import Segment

DISPLAY_FONT = os.environ.get(
    "AUTOSHORTS_DISPLAY_FONT", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
)

CANVAS_W = 1080
CANVAS_H = 1920

# extra zoom-in applied on top of the minimum scale needed to fill the
# canvas (see build_filter_complex's scale stage and find_speaker_crop).
# Without this, a speaker positioned close to either edge of the original
# frame can't always be centered - centering them would require the crop
# window to extend past the edge of the source frame, which isn't possible,
# so the crop clamps to the frame boundary instead and the speaker ends up
# off-center. Scaling the source further before cropping gives every
# position more room (in pixels) between it and the frame edge, so the
# clamp is far less likely to bite. The tradeoff is a bit more digital
# upscaling (softer image) and a good chance of cropping out whatever sits
# in the corners of the original frame - both accepted in exchange for
# reliably keeping the speaker centered.
FACE_CROP_ZOOM = 1.2

# YouTube's Shorts shelf/grid thumbnail tiles crop a vertical video down
# from the top instead of just scaling it - text sitting close to the top
# edge of the canvas gets its top sliced off in those tiles even though it
# looks fine in the full player. TITLE_TOP_MARGIN pushes the title text down
# far enough to clear that crop.
TITLE_BAND_H = 440
TITLE_TOP_MARGIN = 70
TITLE_LINE1_Y = 100 + TITLE_TOP_MARGIN
TITLE_LINE2_Y = 226 + TITLE_TOP_MARGIN
TITLE_FONTSIZE = 88
ACCENT_COLOR = "gold"

LOGO_IMAGE_HEIGHT = 210
# real logo aspect ratios vary; cap width too (fit-in-box, not just height)
# so two side-by-side logos can't overlap or run off a 1080px-wide canvas
LOGO_IMAGE_MAX_WIDTH = 221
LOGO_MARGIN_X = 20
LOGO_MARGIN_Y = 6

# the worker-uploaded bottom band image (step 8), the bottommost item
# stacked in the bottom info band - flush against the very bottom edge of
# the canvas.
BOTTOM_IMAGE_BAND_H = 130
BOTTOM_IMAGE_HEIGHT = 90
BOTTOM_IMAGE_MAX_WIDTH = 900
BOTTOM_IMAGE_BAND_OPACITY = 0.75
BOTTOM_IMAGE_BAND_TOP = CANVAS_H - BOTTOM_IMAGE_BAND_H

# captions sit directly above the bottom image band, still inside the same
# solid-black bottom info band (not a separate overlay floating over video).
CAPTION_FONTSIZE = 62
CAPTION_BAND_H = 200
CAPTION_BAND_GAP_ABOVE_BOTTOM_IMAGE = 40
CAPTION_BAND_TOP = BOTTOM_IMAGE_BAND_TOP - CAPTION_BAND_GAP_ABOVE_BOTTOM_IMAGE - CAPTION_BAND_H
CAPTION_TEXT_PADDING_TOP = 25
CAPTION_LINE_HEIGHT = round(CAPTION_FONTSIZE * 1.25)
CAPTION_MAX_CHARS_PER_LINE = 14
CAPTION_BOX_OPACITY = 0.75

# optional name plate ("이름 직책 / 소속") identifying the on-screen guest,
# shown for the whole clip at the top of the bottom info band, directly
# above the caption.
GUEST_LABEL_FONTSIZE = 38
GUEST_LABEL_BAND_H = 66
GUEST_LABEL_GAP_ABOVE_CAPTION = 8
GUEST_LABEL_BAND_TOP = CAPTION_BAND_TOP - GUEST_LABEL_GAP_ABOVE_CAPTION - GUEST_LABEL_BAND_H
GUEST_LABEL_BOX_OPACITY = 0.75

# the bottom info band - guest name plate + caption + bottom image stacked
# together - is padded into the canvas the same way the top title band is
# (see the `pad` stage in build_filter_complex), instead of floating as a
# translucent overlay on top of the video: that would leave slivers of raw
# video peeking through above/below/between the three elements.
# GUEST_LABEL_BAND_TOP is the topmost of the three stacked elements, so it
# doubles as this band's top edge.
BOTTOM_BAND_H = CANVAS_H - GUEST_LABEL_BAND_TOP


@dataclass
class RenderAssets:
    thumbnail_text: str = ""
    guest_label: str = ""
    logo_top_left: Path | None = None
    logo_top_right: Path | None = None
    banner_bottom: Path | None = None


def escape_drawtext(text: str, keep_newlines: bool = False) -> str:
    """Escape a string for safe use inside an ffmpeg drawtext filter argument.

    Every drawtext call is built with expansion=none (see build_filter_complex),
    which turns off drawtext's own %{...} expansion mini-language entirely -
    that's what a literal '%' in real captions needs to survive intact;
    backslash-escaping it (the naive fix) does not work, since expansion
    runs as a separate parsing pass after this value is already extracted.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "’")  # avoid unbalanced quotes inside the filter
    if not keep_newlines:
        text = text.replace("\n", " ")
    return text


def _title_lines(thumbnail_text: str) -> tuple[str, str | None]:
    """Split thumbnail_text into (line1, line2). line2 is None if not given."""
    parts = thumbnail_text.split("\n", 1)
    line1 = parts[0].strip()
    line2 = parts[1].strip() if len(parts) > 1 else None
    return line1, (line2 or None)


def _wrap_caption(text: str, max_chars: int = CAPTION_MAX_CHARS_PER_LINE) -> str:
    """Wrap caption text onto at most 2 lines, breaking near the midpoint on a space."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    mid = len(text) // 2
    break_at = None
    for offset in range(0, mid + 1):
        for candidate in (mid - offset, mid + offset):
            if 0 < candidate < len(text) and text[candidate] == " ":
                break_at = candidate
                break
        if break_at is not None:
            break
    if break_at is None:
        return text
    return text[:break_at] + "\n" + text[break_at + 1:]


_SPEAKER_MARKER_RE = re.compile(r"\s*>>\s*")


def _strip_speaker_marker(text: str) -> str:
    """Drop the ">>" speaker-change marker analyze.py's guest-centered rule
    relies on in the transcript text - it belongs in the analysis prompt,
    not in what a viewer actually reads on-screen."""
    return _SPEAKER_MARKER_RE.sub(" ", text).strip()


def captions_for_ranges(
    segments: list[Segment], ranges: list[ClipRange]
) -> list[tuple[float, float, str]]:
    """Transcript segments overlapping any of `ranges`, with times mapped
    onto the final concatenated clip's own timeline: each range contributes
    its own overlapping segments (relative to that range's start), offset
    by the total duration of the ranges stitched in before it."""
    out: list[tuple[float, float, str]] = []
    clip_offset = 0.0
    for r in ranges:
        for seg in segments:
            if seg.end_sec <= r.start_sec or seg.start_sec >= r.end_sec:
                continue
            rel_start = max(0.0, seg.start_sec - r.start_sec)
            rel_end = min(r.end_sec - r.start_sec, seg.end_sec - r.start_sec)
            if rel_end <= rel_start:
                continue
            text = _strip_speaker_marker(seg.text)
            if not text:
                continue
            out.append((clip_offset + rel_start, clip_offset + rel_end, text))
        clip_offset += r.end_sec - r.start_sec
    return out


def _face_crop_offset(
    faces: list[tuple[float, float, float, float]],
    scale: float,
    canvas_w: int,
    canvas_h: int,
    scaled_w: float,
    scaled_h: float,
    src_w: float,
) -> tuple[int, int] | None:
    """Given detected face boxes (x, y, w, h) in original-frame pixel coords,
    return the (x, y) crop offset - in scaled-frame pixel coords - that keeps
    the most prominent face(s) inside a canvas_w x canvas_h crop window,
    biased to leave headroom above the face rather than dead-centering it.
    None if no faces were given.

    A static multi-camera composite (e.g. host and guest, each in their own
    camera box side by side) has a face on each side of every sampled frame.
    Averaging all of them just re-centers the crop on the seam between the
    two cameras - exactly where you don't want it. Instead, cluster faces by
    which half of the source frame they're in (split at src_w/2) and go with
    whichever side has faces at all, preferring the right if both do (most
    interview-style formats seat the person actually being quoted on that
    side - see analyze.py's _GUEST_CENTERED_RULE, which this crop bias is
    built to match).
    """
    if not faces:
        return None
    midpoint = src_w / 2
    left = [f for f in faces if f[0] + f[2] / 2 < midpoint]
    right = [f for f in faces if f[0] + f[2] / 2 >= midpoint]
    dominant = right if right else left

    total_area = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    for x, y, w, h in dominant:
        area = w * h
        weighted_x += (x + w / 2) * area
        weighted_y += (y + h * 0.35) * area  # roughly eye-level, not box center
        total_area += area
    face_x = (weighted_x / total_area) * scale
    face_y = (weighted_y / total_area) * scale

    max_x_off = max(0.0, scaled_w - canvas_w)
    max_y_off = max(0.0, scaled_h - canvas_h)
    x_off = min(max(0.0, face_x - canvas_w / 2), max_x_off)
    y_off = min(max(0.0, face_y - canvas_h / 3), max_y_off)
    return round(x_off), round(y_off)


# tried in order; side-lit/angled shots are often missed by the default
# frontal cascade alone
_FACE_CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")


def _detect_faces(image_path: Path) -> list[tuple[float, float, float, float]]:
    """Detect faces in a single image, returning (x, y, w, h) boxes in pixel
    coords. Empty on any failure (no opencv installed, a build/version that
    dropped CascadeClassifier, unreadable image, etc.) - callers treat that
    the same as "no faces found"."""
    try:
        import cv2
    except ImportError as e:
        print(f"[render] opencv를 불러올 수 없어 얼굴 인식을 건너뜁니다: {e}")
        return []
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return []
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        boxes: list[tuple[float, float, float, float]] = []
        for cascade_name in _FACE_CASCADES:
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + cascade_name)
            found = cascade.detectMultiScale(
                gray, scaleFactor=1.05, minNeighbors=3, minSize=(30, 30)
            )
            boxes.extend((float(x), float(y), float(w), float(h)) for x, y, w, h in found)
        return boxes
    except (AttributeError, cv2.error) as e:
        print(f"[render] 얼굴 인식 실패, 가운데 크롭을 사용합니다: {e}")
        return []


def _probe_dimensions(video_path: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=p=0",
                str(video_path),
            ],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        w, h = (int(v) for v in out.split(","))
        return w, h
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


_CENTER_CROP = ("(in_w-out_w)/2", "(in_h-out_h)/2")


def find_speaker_crop(clip_path: Path, canvas_w: int, canvas_h: int) -> tuple[str, str]:
    """Best-effort: sample a few frames from clip_path, detect faces, and
    return (crop_x, crop_y) ffmpeg crop-filter position values that keep the
    most prominent speaker in frame instead of a blind center crop. Falls
    back to ffmpeg's own centered crop on any failure (opencv not installed,
    no faces found, ffprobe/ffmpeg failure, etc.) - never raises.
    """
    try:
        import cv2  # noqa: F401
    except ImportError as e:
        print(f"[render] opencv 미설치/로드 실패로 가운데 크롭을 사용합니다: {e}")
        return _CENTER_CROP

    dims = _probe_dimensions(clip_path)
    if dims is None:
        print("[render] ffprobe 실패로 가운데 크롭을 사용합니다.")
        return _CENTER_CROP
    src_w, src_h = dims
    # FACE_CROP_ZOOM here must exactly match the extra zoom build_filter_complex
    # applies to its own scale stage - otherwise the offsets computed here
    # would reference a different scaled frame than the one ffmpeg actually
    # produces, and the crop would land on the wrong region entirely.
    scale = max(canvas_w / src_w, canvas_h / src_h) * FACE_CROP_ZOOM
    scaled_w, scaled_h = src_w * scale, src_h * scale

    frames_dir = clip_path.parent / "face_sample_frames"
    frames_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(clip_path), "-vf", "fps=1/3",
                str(frames_dir / "f_%03d.jpg"),
            ],
            check=True, capture_output=True,
        )
        all_faces: list[tuple[float, float, float, float]] = []
        for frame_path in sorted(frames_dir.glob("f_*.jpg")):
            all_faces.extend(_detect_faces(frame_path))
    except subprocess.CalledProcessError as e:
        print(f"[render] 프레임 추출 실패로 가운데 크롭을 사용합니다: {e}")
        return _CENTER_CROP
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    offset = _face_crop_offset(all_faces, scale, canvas_w, canvas_h, scaled_w, scaled_h, src_w)
    if offset is None:
        print("[render] 샘플 프레임에서 얼굴을 찾지 못해 가운데 크롭을 사용합니다.")
        return _CENTER_CROP
    print(f"[render] 얼굴 {len(all_faces)}개 인식, 크롭 위치 조정: x={offset[0]}, y={offset[1]}")
    return str(offset[0]), str(offset[1])


def _logo_content_bbox(image_path: Path) -> tuple[int, int, int, int] | None:
    """Return the (left, top, right, bottom) pixel box of an image's
    non-transparent content, or None if it can't be determined.

    Uploaded logo art is often exported on a larger canvas than the visible
    mark itself, padded with transparent pixels. Left uncropped, that
    padding scales along with the logo and reappears as visible empty space
    once overlaid - fixing it requires cropping to the actual content
    before scaling, not just tightening the overlay margin.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(image_path) as img:
            alpha = img.convert("RGBA").split()[-1]
            return alpha.getbbox()
    except Exception:
        return None


def _logo_overlay_y(bbox: tuple[int, int, int, int] | None) -> int:
    """Vertical overlay offset for a top-corner logo, centered within the
    shared LOGO_IMAGE_HEIGHT box instead of just top-aligned - two logos of
    different aspect ratios scaled to fit the same box
    (force_original_aspect_ratio=decrease) end up at different actual
    heights, so top-aligning both would put their centers out of line.
    Falls back to the plain top-aligned offset when bbox is unavailable
    (PIL missing), since the scaled height can't be computed without it.
    """
    base_y = TITLE_BAND_H + LOGO_MARGIN_Y
    if bbox is None:
        return base_y
    content_w, content_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    scale = min(LOGO_IMAGE_MAX_WIDTH / content_w, LOGO_IMAGE_HEIGHT / content_h)
    scaled_h = content_h * scale
    return base_y + round((LOGO_IMAGE_HEIGHT - scaled_h) / 2)


def _bottom_image_overlay_y(bbox: tuple[int, int, int, int] | None) -> int:
    """Vertical overlay offset that centers the bottom band image within
    the BOTTOM_IMAGE_BAND_H band at BOTTOM_IMAGE_BAND_TOP, analogous to
    _logo_overlay_y for the top-corner logos."""
    base_y = BOTTOM_IMAGE_BAND_TOP
    if bbox is None:
        return base_y
    content_w, content_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    scale = min(BOTTOM_IMAGE_MAX_WIDTH / content_w, BOTTOM_IMAGE_HEIGHT / content_h)
    scaled_h = content_h * scale
    return base_y + round((BOTTOM_IMAGE_BAND_H - scaled_h) / 2)


def build_filter_complex(
    thumbnail_text: str = "",
    captions: list[tuple[float, float, str]] | None = None,
    display_font_path: str = DISPLAY_FONT,
    logo_top_left: Path | None = None,
    logo_top_right: Path | None = None,
    banner_bottom: Path | None = None,
    guest_label_text: str | None = None,
    crop_x: str = _CENTER_CROP[0],
    crop_y: str = _CENTER_CROP[1],
) -> tuple[str, str, str, list[Path]]:
    """Return (filter_complex, video_map_label, audio_map_label, extra_inputs).

    crop_x/crop_y are ffmpeg crop-filter position expressions (or plain
    pixel offsets), in the coordinate space of the scaled frame. Defaults
    to a plain center crop; the caller overrides them with a face-detected
    offset when possible so a speaker isn't cropped out by a blind center
    crop.

    guest_label_text, if given, is a single pre-formatted line (e.g. "김은비
    변호사 / 손해보험협회") shown for the whole clip just above the caption
    band, identifying who's on screen.

    extra_inputs is the ordered list of image files (logo_top_left,
    logo_top_right, banner_bottom, in that order, whichever are given) the
    caller must also pass as ffmpeg "-i" arguments right after the main
    video input - the filter graph references them as [1:v], [2:v], etc.
    """
    captions = captions or []
    # the video sits between the top title band and the bottom info band
    # (guest name/caption/bottom image, see BOTTOM_BAND_H) - both padded in
    # as solid black rather than overlaid on top of the video, so it's
    # cropped to fill only the space actually left between them
    video_h = CANVAS_H - TITLE_BAND_H - BOTTOM_BAND_H
    extra_inputs: list[Path] = []

    # scale to FACE_CROP_ZOOM's inflated target, not the plain canvas size -
    # see find_speaker_crop, which computes crop_x/crop_y against this same
    # inflated scale so the two stay in sync
    zoom_w = round(CANVAS_W * FACE_CROP_ZOOM)
    zoom_h = round(video_h * FACE_CROP_ZOOM)
    stages = [
        f"[0:v]scale={zoom_w}:{zoom_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={CANVAS_W}:{video_h}:{crop_x}:{crop_y}[cropped]",
        # video sits between the title band and the bottom info band;
        # padding to the full canvas leaves both as black automatically
        f"[cropped]pad={CANVAS_W}:{CANVAS_H}:0:{TITLE_BAND_H}:color=black[padded]",
    ]

    line1, line2 = _title_lines(thumbnail_text)
    last = "padded"
    stages.append(
        f"[{last}]drawtext=fontfile={display_font_path}:expansion=none:text='{escape_drawtext(line1)}':"
        f"fontcolor=white:fontsize={TITLE_FONTSIZE}:"
        f"x=(w-text_w)/2:y={TITLE_LINE1_Y}[t1]"
    )
    last = "t1"
    if line2:
        stages.append(
            f"[{last}]drawtext=fontfile={display_font_path}:expansion=none:text='{escape_drawtext(line2)}':"
            f"fontcolor={ACCENT_COLOR}:fontsize={TITLE_FONTSIZE}:"
            f"x=(w-text_w)/2:y={TITLE_LINE2_Y}[t2]"
        )
        last = "t2"

    if logo_top_left is not None and logo_top_left.is_file():
        extra_inputs.append(logo_top_left)
        idx = len(extra_inputs)
        bbox = _logo_content_bbox(logo_top_left)
        crop_stage = (
            f"crop={bbox[2] - bbox[0]}:{bbox[3] - bbox[1]}:{bbox[0]}:{bbox[1]}," if bbox else ""
        )
        stages.append(
            f"[{idx}:v]{crop_stage}scale=w={LOGO_IMAGE_MAX_WIDTH}:h={LOGO_IMAGE_HEIGHT}:"
            f"force_original_aspect_ratio=decrease[lgimg{idx}]"
        )
        stages.append(
            f"[{last}][lgimg{idx}]overlay=x={LOGO_MARGIN_X}:y={_logo_overlay_y(bbox)}[lg1]"
        )
        last = "lg1"

    if logo_top_right is not None and logo_top_right.is_file():
        extra_inputs.append(logo_top_right)
        idx = len(extra_inputs)
        bbox = _logo_content_bbox(logo_top_right)
        crop_stage = (
            f"crop={bbox[2] - bbox[0]}:{bbox[3] - bbox[1]}:{bbox[0]}:{bbox[1]}," if bbox else ""
        )
        stages.append(
            f"[{idx}:v]{crop_stage}scale=w={LOGO_IMAGE_MAX_WIDTH}:h={LOGO_IMAGE_HEIGHT}:"
            f"force_original_aspect_ratio=decrease[lgimg{idx}]"
        )
        stages.append(
            f"[{last}][lgimg{idx}]overlay=x=main_w-overlay_w-{LOGO_MARGIN_X}:"
            f"y={_logo_overlay_y(bbox)}[lg2]"
        )
        last = "lg2"

    if banner_bottom is not None and banner_bottom.is_file():
        stages.append(
            f"[{last}]drawbox=x=0:y={BOTTOM_IMAGE_BAND_TOP}:w={CANVAS_W}:h={BOTTOM_IMAGE_BAND_H}:"
            f"color=black@{BOTTOM_IMAGE_BAND_OPACITY}:t=fill[botbox]"
        )
        last = "botbox"
        extra_inputs.append(banner_bottom)
        idx = len(extra_inputs)
        bbox = _logo_content_bbox(banner_bottom)
        crop_stage = (
            f"crop={bbox[2] - bbox[0]}:{bbox[3] - bbox[1]}:{bbox[0]}:{bbox[1]}," if bbox else ""
        )
        stages.append(
            f"[{idx}:v]{crop_stage}scale=w={BOTTOM_IMAGE_MAX_WIDTH}:h={BOTTOM_IMAGE_HEIGHT}:"
            f"force_original_aspect_ratio=decrease[botimg{idx}]"
        )
        stages.append(
            f"[{last}][botimg{idx}]overlay=x=(main_w-overlay_w)/2:y={_bottom_image_overlay_y(bbox)}[botimg]"
        )
        last = "botimg"

    if guest_label_text:
        stages.append(
            f"[{last}]drawbox=x=0:y={GUEST_LABEL_BAND_TOP}:w={CANVAS_W}:h={GUEST_LABEL_BAND_H}:"
            f"color=black@{GUEST_LABEL_BOX_OPACITY}:t=fill[guestbox]"
        )
        stages.append(
            f"[guestbox]drawtext=fontfile={display_font_path}:expansion=none:"
            f"text='{escape_drawtext(guest_label_text)}':"
            f"fontcolor={ACCENT_COLOR}:fontsize={GUEST_LABEL_FONTSIZE}:"
            f"x=(w-text_w)/2:y={GUEST_LABEL_BAND_TOP}+({GUEST_LABEL_BAND_H}-text_h)/2[guestlabel]"
        )
        last = "guestlabel"

    for i, (cue_start, cue_end, text) in enumerate(captions):
        enable = f"enable='between(t,{cue_start},{cue_end})'"
        wrapped = _wrap_caption(text)
        cap_lines = wrapped.split("\n", 1)
        cap_line1 = cap_lines[0]
        cap_line2 = cap_lines[1] if len(cap_lines) > 1 else None

        band_label = f"capband{i}"
        stages.append(
            f"[{last}]drawbox=x=0:y={CAPTION_BAND_TOP}:w={CANVAS_W}:h={CAPTION_BAND_H}:"
            f"color=black@{CAPTION_BOX_OPACITY}:t=fill:{enable}[{band_label}]"
        )
        last = band_label

        line1_y = CAPTION_BAND_TOP + CAPTION_TEXT_PADDING_TOP
        label = f"cap{i}a"
        stages.append(
            f"[{last}]drawtext=fontfile={display_font_path}:expansion=none:text='{escape_drawtext(cap_line1)}':"
            f"fontcolor=white:fontsize={CAPTION_FONTSIZE}:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={line1_y}:{enable}[{label}]"
        )
        last = label

        if cap_line2:
            line2_y = line1_y + CAPTION_LINE_HEIGHT
            label2 = f"cap{i}b"
            stages.append(
                f"[{last}]drawtext=fontfile={display_font_path}:expansion=none:text='{escape_drawtext(cap_line2)}':"
                f"fontcolor=white:fontsize={CAPTION_FONTSIZE}:borderw=3:bordercolor=black:"
                f"x=(w-text_w)/2:y={line2_y}:{enable}[{label2}]"
            )
            last = label2

    stages.append(f"[{last}]null[vout]")
    stages.append("[0:a]anull[aout]")

    return ";".join(stages), "[vout]", "[aout]", extra_inputs


def _run_ffmpeg(
    clip_path: Path,
    work_dir: Path,
    out_path: Path,
    filter_complex: str,
    v_label: str,
    a_label: str,
    extra_inputs: list[Path],
    crf: int,
    preset: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(clip_path)]
    for image_path in extra_inputs:
        cmd += ["-i", str(image_path)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", v_label,
        "-map", a_label,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, cwd=work_dir)


def _local_font(display_font_path: str, work_dir: Path) -> str:
    """ffmpeg's filter option syntax uses ':' as a key=value separator, so a
    raw font path containing one (e.g. a Windows drive letter like C:\\...)
    needs escaping - and that escaping is finicky and inconsistent across
    ffmpeg versions. Sidestep it entirely: copy the font next to the clip
    and run ffmpeg with that as its cwd, so the filter can reference it by
    a bare filename that never contains a colon."""
    local_path = work_dir / f"display_font{Path(display_font_path).suffix}"
    shutil.copyfile(display_font_path, local_path)
    return local_path.name


def render_draft(
    clip_path: Path,
    captions: list[tuple[float, float, str]],
    out_path: Path,
    work_dir: Path | None = None,
) -> Path:
    """Fast preview: crop + burned-in captions only, no title banner, logos,
    or guest label - just enough for the worker to proofread captions
    against picture (step 7). Uses a plain center crop (skipping the
    face-detection frame sampling) and a faster encode preset, since
    accurate framing doesn't matter yet at this stage.

    `captions` is the (start, end, text) cue list for the built clip's own
    timeline - get the initial version from captions_for_ranges(segments,
    ranges), shown to the worker for proofreading; pass it back in
    (with corrected text, same timings) to render_final so the fix carries
    through to the final render.
    """
    work_dir = work_dir or out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    local_display_font = _local_font(DISPLAY_FONT, work_dir)

    filter_complex, v_label, a_label, extra_inputs = build_filter_complex(
        captions=captions, display_font_path=local_display_font,
    )
    _run_ffmpeg(
        clip_path, work_dir, out_path, filter_complex, v_label, a_label, extra_inputs,
        crf=23, preset="veryfast",
    )
    return out_path


def render_final(
    clip_path: Path,
    captions: list[tuple[float, float, str]],
    assets: RenderAssets,
    out_path: Path,
    work_dir: Path | None = None,
) -> Path:
    """Full composite (step 9): face-detected crop, title banner, uploaded
    logos/bottom image, guest label, and captions - pass in the same
    (start, end, text) cues shown at the draft stage, with the worker's
    typo corrections applied to the text (see render_draft)."""
    work_dir = work_dir or out_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    local_display_font = _local_font(DISPLAY_FONT, work_dir)

    video_h = CANVAS_H - TITLE_BAND_H - BOTTOM_BAND_H
    crop_x, crop_y = find_speaker_crop(clip_path, CANVAS_W, video_h)

    filter_complex, v_label, a_label, extra_inputs = build_filter_complex(
        thumbnail_text=assets.thumbnail_text,
        captions=captions,
        display_font_path=local_display_font,
        logo_top_left=assets.logo_top_left,
        logo_top_right=assets.logo_top_right,
        banner_bottom=assets.banner_bottom,
        guest_label_text=assets.guest_label or None,
        crop_x=crop_x, crop_y=crop_y,
    )
    _run_ffmpeg(
        clip_path, work_dir, out_path, filter_complex, v_label, a_label, extra_inputs,
        crf=18, preset="slow",
    )
    return out_path

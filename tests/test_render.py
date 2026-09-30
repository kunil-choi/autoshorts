from autoshorts.analyze import ClipRange
from autoshorts.render import (
    build_filter_complex,
    captions_for_ranges,
    escape_drawtext,
    _blind_right_biased_offset,
    _clip_overlapping_cues,
    _face_crop_offset,
    _title_lines,
    _wrap_caption,
)
from autoshorts.transcript import Segment


def test_escape_drawtext_escapes_colon_and_backslash():
    assert escape_drawtext("20%p 폭락") == "20%p 폭락"
    assert escape_drawtext("C:\\path") == "C\\:\\\\path"


def test_escape_drawtext_replaces_newlines_by_default():
    assert escape_drawtext("한 줄\n두 줄") == "한 줄 두 줄"


def test_escape_drawtext_can_keep_newlines():
    assert escape_drawtext("한 줄\n두 줄", keep_newlines=True) == "한 줄\n두 줄"


def test_title_lines_splits_on_first_newline():
    assert _title_lines("로봇택시\n취객은 누가 깨울까?") == ("로봇택시", "취객은 누가 깨울까?")
    assert _title_lines("한 줄만") == ("한 줄만", None)


def test_wrap_caption_leaves_short_text_alone():
    assert _wrap_caption("짧은 자막") == "짧은 자막"


def test_wrap_caption_breaks_long_text_near_midpoint_on_space():
    text = "이것은 상당히 긴 자막 문장이라 두 줄로 나눠야 한다"
    wrapped = _wrap_caption(text, max_chars=14)
    assert "\n" in wrapped
    line1, line2 = wrapped.split("\n")
    assert line1 + " " + line2 == text


def test_captions_for_ranges_offsets_each_range_by_prior_durations():
    segments = [
        Segment(0.0, 3.0, "a"),
        Segment(3.0, 5.0, "b"),
        Segment(6.0, 9.0, "c"),
    ]
    ranges = [ClipRange(1.0, 4.0), ClipRange(6.0, 8.0)]
    cues = captions_for_ranges(segments, ranges)
    # range 1 [1,4): "a" -> (0,2), "b" -> (2,3); range 2 [6,8) starts at
    # clip_offset=3.0 (4.0-1.0): "c" -> (3,5)
    assert cues == [(0.0, 2.0, "a"), (2.0, 3.0, "b"), (3.0, 5.0, "c")]


def test_captions_for_ranges_drops_segments_outside_every_range():
    segments = [Segment(0.0, 5.0, "in"), Segment(20.0, 25.0, "out")]
    cues = captions_for_ranges(segments, [ClipRange(0.0, 5.0)])
    assert cues == [(0.0, 5.0, "in")]


def test_build_filter_complex_includes_title_and_caption_text():
    filter_complex, v_label, a_label, extra_inputs = build_filter_complex(
        thumbnail_text="제목1\n제목2",
        captions=[(0.5, 2.0, "자막 문장")],
    )
    assert "제목1" in filter_complex
    assert "제목2" in filter_complex
    assert "자막 문장" in filter_complex
    assert v_label == "[vout]"
    assert a_label == "[aout]"
    assert extra_inputs == []


def test_build_filter_complex_skips_decorations_when_omitted():
    filter_complex, _, _, extra_inputs = build_filter_complex(captions=[])
    assert "drawbox" not in filter_complex  # no guest label / bottom image band
    assert extra_inputs == []


def test_clip_overlapping_cues_trims_end_to_next_start():
    # YouTube rolling auto-captions routinely overlap like this - without
    # clipping, both cues would satisfy their enable() window at once and
    # burn in as a dissolve/overlap instead of a clean cut.
    cues = [(0.0, 3.0, "a"), (2.0, 5.0, "b"), (4.5, 8.0, "c")]
    assert _clip_overlapping_cues(cues) == [(0.0, 2.0, "a"), (2.0, 4.5, "b"), (4.5, 8.0, "c")]


def test_clip_overlapping_cues_drops_fully_swallowed_cue():
    # a cue entirely inside the next one's overlap window would clip to a
    # zero/negative duration - drop it rather than emit an invalid cue.
    cues = [(0.0, 5.0, "a"), (1.0, 2.0, "b"), (1.0, 6.0, "c")]
    assert _clip_overlapping_cues(cues) == [(0.0, 1.0, "a"), (1.0, 6.0, "c")]


def test_captions_for_ranges_excludes_deselected_segment_despite_overlap():
    # a worker deselecting the middle segment of a group splits it into two
    # ranges around the deselected one - its own overlap with either
    # range's edge (from segments overlapping their neighbors) must not be
    # enough to sneak its caption into either range's video.
    segments = [
        Segment(0.0, 5.0, "keep-a"),
        Segment(4.0, 9.0, "deselected"),
        Segment(8.0, 12.0, "keep-b"),
    ]
    ranges = [ClipRange(0.0, 5.0), ClipRange(8.0, 12.0)]
    cues = captions_for_ranges(segments, ranges)
    texts = [text for _, _, text in cues]
    assert texts == ["keep-a", "keep-b"]


def test_captions_for_ranges_clips_overlapping_transcript_segments():
    segments = [
        Segment(0.0, 3.0, "a"),
        Segment(2.0, 5.0, "b"),
    ]
    cues = captions_for_ranges(segments, [ClipRange(0.0, 5.0)])
    assert cues == [(0.0, 2.0, "a"), (2.0, 5.0, "b")]


def test_face_crop_offset_prefers_right_side_faces():
    faces = [(10.0, 10.0, 50.0, 50.0), (200.0, 10.0, 50.0, 50.0)]
    offset = _face_crop_offset(
        faces, scale=1.0, canvas_w=100, canvas_h=100, scaled_w=300.0, scaled_h=300.0, src_w=300.0,
    )
    assert offset is not None
    # right face center is x=225, so the crop window (100 wide) should be
    # pulled toward that side rather than sitting at the frame's midpoint
    assert offset[0] > 100  # would be ~100 for a plain center crop


def test_blind_right_biased_offset_is_right_of_center():
    x_off, _ = _blind_right_biased_offset(
        scale=1.0, canvas_w=100, canvas_h=100, scaled_w=300.0, scaled_h=300.0, src_w=300.0,
    )
    center_x_off = (300.0 - 100) / 2  # what a plain center crop would use
    assert x_off > center_x_off

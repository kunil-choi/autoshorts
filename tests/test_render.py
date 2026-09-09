from autoshorts.analyze import ClipRange
from autoshorts.render import (
    build_filter_complex,
    captions_for_ranges,
    escape_drawtext,
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

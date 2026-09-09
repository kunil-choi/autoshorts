from autoshorts.analyze import (
    Candidate,
    ClipRange,
    _closest_boundary,
    _custom_topic_philosophy,
    _hms_to_sec,
    _sec_to_hms,
    _snap_to_segment_boundaries,
)
from autoshorts.transcript import Segment


def test_hms_to_sec_and_back():
    assert _hms_to_sec("01:02:03") == 3723.0
    assert _hms_to_sec("02:03") == 123.0
    assert _sec_to_hms(3723) == "01:02:03"
    assert _sec_to_hms(123) == "02:03"


def test_closest_boundary_snaps_within_tolerance():
    boundaries = [0.0, 5.0, 10.0]
    assert _closest_boundary(5.4, boundaries) == 5.0
    # far outside tolerance (1.5s) - left as-is
    assert _closest_boundary(7.0, boundaries) == 7.0


def test_closest_boundary_empty_list_is_noop():
    assert _closest_boundary(5.0, []) == 5.0


def test_snap_to_segment_boundaries_corrects_all_ranges_in_a_candidate():
    segments = [Segment(0, 5, "a"), Segment(5, 10, "b"), Segment(10, 15, "c")]
    candidates = [
        Candidate(
            ranges=[ClipRange(0.2, 4.9), ClipRange(10.3, 14.8)],
            title="t", summary="s", thumbnail_text="x", reason="r", tier="hook",
        )
    ]
    snapped = _snap_to_segment_boundaries(candidates, segments)
    assert snapped[0].ranges[0] == ClipRange(0.0, 5.0)
    assert snapped[0].ranges[1] == ClipRange(10.0, 15.0)


def test_custom_topic_philosophy_embeds_topic_and_count():
    text = _custom_topic_philosophy("금리 인상 관련 발언", count=3)
    assert "금리 인상 관련 발언" in text
    assert "3개보다 적게" in text

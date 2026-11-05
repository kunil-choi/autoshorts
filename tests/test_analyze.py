from types import SimpleNamespace

from autoshorts.analyze import (
    Candidate,
    ClipRange,
    _closest_boundary,
    _custom_topic_philosophy,
    _hms_to_sec,
    _sec_to_hms,
    _snap_to_segment_boundaries,
    propose_candidates,
)
from autoshorts.config import ClipLengthPreset
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


class _RecordingClient:
    """Stands in for anthropic.Anthropic: records each messages.create call
    and answers with one valid candidate via the forced tool."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool_use = SimpleNamespace(type="tool_use", input={"candidates": [{
            "start_hms": "00:00", "end_hms": "00:20", "title": "t", "summary": "s",
            "thumbnail_text": "a\nb", "reason": "r",
        }]})
        return SimpleNamespace(content=[tool_use])


def _segments():
    return [Segment(float(i * 10), float(i * 10 + 10), f"문장 {i}") for i in range(6)]


def _preset():
    return ClipLengthPreset(key="test", label="test", min_sec=15, max_sec=45)


def test_tier_calls_share_a_cached_transcript_prefix():
    client = _RecordingClient()
    propose_candidates(_segments(), _preset(), ["hook", "substantive"], video_title="제목", client=client)
    hook, substantive = client.calls
    assert hook["tools"] == substantive["tools"]
    assert hook["system"][0] == substantive["system"][0]
    assert hook["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "문장 5" in hook["system"][0]["text"] and "제목" in hook["system"][0]["text"]
    assert hook["system"][1]["text"] != substantive["system"][1]["text"]
    assert "문장 5" not in hook["messages"][0]["content"]


def test_single_tier_run_skips_the_cache_write():
    client = _RecordingClient()
    propose_candidates(_segments(), _preset(), ["hook"], client=client)
    assert "cache_control" not in client.calls[0]["system"][0]



# --- extract_guest_info uses the light model, with fallback to MODEL ---------

class _FakeMessages:
    """messages.create stand-in: records the model per call; `fail_models`
    raise, everything else returns a found-guest tool_use block."""

    def __init__(self, fail_models=()):
        self.models = []
        self.fail_models = set(fail_models)

    def create(self, model, **kwargs):
        self.models.append(model)
        if model in self.fail_models:
            raise RuntimeError("model unavailable")
        block = SimpleNamespace(
            type="tool_use",
            input={"found": True, "name": "김은비", "title": "변호사", "org": "손해보험협회"},
        )
        return SimpleNamespace(content=[block])


def test_extract_guest_info_uses_light_model():
    from autoshorts import analyze

    fake = SimpleNamespace(messages=_FakeMessages())
    out = analyze.extract_guest_info("제목", "게스트: 김은비 변호사", client=fake)
    assert out == "김은비 변호사 / 손해보험협회"
    assert fake.messages.models == [analyze.LIGHT_MODEL]


def test_extract_guest_info_falls_back_to_main_model_when_light_model_fails():
    from autoshorts import analyze

    if analyze.LIGHT_MODEL == analyze.MODEL:
        return  # split turned off via env - nothing to fall back from
    fake = SimpleNamespace(messages=_FakeMessages(fail_models={analyze.LIGHT_MODEL}))
    out = analyze.extract_guest_info("제목", "게스트: 김은비 변호사", client=fake)
    assert out == "김은비 변호사 / 손해보험협회"
    assert fake.messages.models == [analyze.LIGHT_MODEL, analyze.MODEL]


def test_extract_guest_info_empty_description_makes_no_call():
    from autoshorts import analyze

    fake = SimpleNamespace(messages=_FakeMessages())
    assert analyze.extract_guest_info("제목", None, client=fake) == ""
    assert fake.messages.models == []


def test_other_steps_still_use_main_model():
    import inspect
    from autoshorts import analyze

    # only extract_guest_info may reference LIGHT_MODEL
    for fn in (analyze.label_speakers, analyze.correct_caption_errors,
               analyze._refine_one, analyze._propose_candidates_for_tier):
        assert "LIGHT_MODEL" not in inspect.getsource(fn), fn.__name__

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from autoshorts import youtube_upload


def _client_raising(status: int) -> MagicMock:
    resp = MagicMock(status=status, reason="x")
    client = MagicMock()
    client.videos().delete().execute.side_effect = HttpError(resp, b"{}")
    return client


def test_delete_video_treats_already_deleted_as_success(monkeypatch):
    monkeypatch.setattr(youtube_upload, "_youtube_client", lambda *a: _client_raising(404))
    youtube_upload.delete_video("gone", Path("cs.json"), Path("tok.json"))  # no raise


def test_delete_video_still_raises_other_errors(monkeypatch):
    monkeypatch.setattr(youtube_upload, "_youtube_client", lambda *a: _client_raising(403))
    with pytest.raises(HttpError):
        youtube_upload.delete_video("v", Path("cs.json"), Path("tok.json"))


def test_get_privacy_statuses_batches_by_50_and_omits_missing(monkeypatch):
    client = MagicMock()
    client.videos().list().execute.side_effect = [
        {"items": [{"id": f"v{i}", "status": {"privacyStatus": "public"}} for i in range(50)]},
        {"items": [{"id": "v50", "status": {"privacyStatus": "private"}}]},
    ]
    monkeypatch.setattr(youtube_upload, "_youtube_client", lambda *a: client)

    ids = [f"v{i}" for i in range(52)]  # v51 is not returned (e.g. deleted)
    result = youtube_upload.get_privacy_statuses(ids, Path("cs.json"), Path("tok.json"))

    assert result["v0"] == "public" and result["v50"] == "private"
    assert "v51" not in result
    assert client.videos().list().execute.call_count == 2

"""Upload a rendered short to YouTube via the YouTube Data API v3, used by
the fully-automatic work path (webui/server.py's /auto-jobs) to publish
straight through without a worker manually using Studio.

Requires a Google Cloud OAuth client (client_secret.json) for a project
with the YouTube Data API v3 enabled, placed next to the app's .env. The
first upload on a machine opens a real browser window for whoever's
running it to sign into the department's YouTube channel once (installed-
app OAuth flow - this is a local desktop tool, same as the rest of the
app); after that, a refresh token is cached on disk and reused silently.
"""

from __future__ import annotations

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_credentials(client_secret_path: Path, token_path: Path):
    """Load cached credentials, refreshing or running the one-time browser
    consent flow as needed. Raises RuntimeError with a Korean, actionable
    message if client_secret_path is missing - there's no way to proceed
    without it, and the raw google-auth error for a missing file isn't
    helpful to a non-technical worker.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret_path.is_file():
        raise RuntimeError(
            f"유튜브 업로드용 인증 파일을 찾을 수 없습니다: {client_secret_path}. "
            "구글 클라우드 콘솔에서 발급받은 client_secret.json 파일을 이 경로에 놓아주세요."
        )

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return creds


def upload_short(
    video_path: Path,
    title: str,
    description: str,
    client_secret_path: Path,
    token_path: Path,
    privacy_status: str = "private",
) -> str:
    """Upload video_path to YouTube and return its watch URL. Raises on any
    failure (missing credentials, API error, quota exceeded) rather than
    swallowing it - unlike analyze.py's best-effort Claude helpers, there's
    no sensible fallback for "the upload didn't happen": the auto-pipeline
    calling this needs to know so it can mark that item as failed instead
    of silently reporting success without a real upload.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials(client_secret_path, token_path)
    # static_discovery (the default when googleapiclient ships a bundled
    # discovery doc, which it does for "youtube v3") avoids an extra network
    # round-trip to Google's discovery service on every single upload.
    youtube = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": "25",  # News & Politics - closest fit for broadcast clips
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _status, response = request.next_chunk()
    video_id = response["id"]
    return f"https://youtu.be/{video_id}"

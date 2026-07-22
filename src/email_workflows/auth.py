"""OAuth credential lifecycle and Google API service construction."""

from __future__ import annotations

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/pubsub",
]


def begin_oauth(client_secret_path: str, redirect_uri: str) -> tuple[str, str, str]:
    flow = Flow.from_client_secrets_file(
        str(Path(client_secret_path).expanduser()),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=True,
    )
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent select_account",
    )
    return url, state, flow.code_verifier or ""


def finish_oauth(
    client_secret_path: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
    authorization_response: str,
    token_path: str | Path,
) -> Credentials:
    flow = Flow.from_client_secrets_file(
        str(Path(client_secret_path).expanduser()),
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
    )
    flow.fetch_token(authorization_response=authorization_response)
    save_credentials(flow.credentials, token_path)
    return flow.credentials


def load_credentials(token_path: str | Path) -> Credentials:
    path = Path(token_path).expanduser()
    credentials = Credentials.from_authorized_user_file(path, SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_credentials(credentials, path)
    if not credentials.valid:
        raise RuntimeError("Google OAuth credentials are invalid; reconnect the account")
    return credentials


def save_credentials(credentials: Credentials, token_path: str | Path) -> None:
    path = Path(token_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(path, 0o600)


def build_services(credentials: Credentials):
    return (
        build("gmail", "v1", credentials=credentials, cache_discovery=False),
        build("pubsub", "v1", credentials=credentials, cache_discovery=False),
    )


def oauth_client_project(client_secret_path: str) -> str:
    data = json.loads(Path(client_secret_path).expanduser().read_text(encoding="utf-8"))
    client = data.get("installed") or data.get("web") or {}
    return str(client.get("project_id", ""))

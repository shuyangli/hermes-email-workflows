from __future__ import annotations

import stat

from email_workflows.auth import PUBSUB_SCOPES, SCOPES, save_credentials


def test_gmail_oauth_does_not_grant_cloud_pubsub_authority():
    assert SCOPES == ["https://www.googleapis.com/auth/gmail.modify"]
    assert PUBSUB_SCOPES == ["https://www.googleapis.com/auth/pubsub"]
    assert set(SCOPES).isdisjoint(PUBSUB_SCOPES)


def test_saved_oauth_token_is_private(tmp_path):
    class Credentials:
        def to_json(self):
            return '{"refresh_token":"secret"}'

    path = tmp_path / "private" / "token.json"
    save_credentials(Credentials(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

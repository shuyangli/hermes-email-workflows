from __future__ import annotations

import os
import stat
import threading

import pytest

from email_workflows.auth import (
    PUBSUB_SCOPES,
    SCOPES,
    _loopback_http_oauth,
    _validate_oauth_client_endpoints,
    save_credentials,
)


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


def test_loopback_oauth_http_override_is_scoped(monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)

    with _loopback_http_oauth("http://127.0.0.1:8787/oauth/callback"):
        assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_non_loopback_http_redirect_is_rejected(monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)

    with (
        pytest.raises(ValueError, match="loopback"),
        _loopback_http_oauth("http://example.com/oauth/callback"),
    ):
        pass


def test_loopback_oauth_restores_preexisting_environment_value(monkeypatch):
    monkeypatch.setenv("OAUTHLIB_INSECURE_TRANSPORT", "existing")

    with _loopback_http_oauth("http://localhost:8787/oauth/callback"):
        assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"

    assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "existing"


def test_loopback_oauth_serializes_overlapping_contexts(monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first():
        with _loopback_http_oauth("http://127.0.0.1:8787/oauth/callback"):
            first_entered.set()
            assert release_first.wait(timeout=5)

    def second():
        assert first_entered.wait(timeout=5)
        with _loopback_http_oauth("http://localhost:8787/oauth/callback"):
            second_entered.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=5)
    try:
        assert not second_entered.wait(timeout=0.1)
    finally:
        release_first.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()
    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_loopback_oauth_restores_environment_after_exception(monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)

    with (
        pytest.raises(RuntimeError, match="boom"),
        _loopback_http_oauth("http://127.0.0.1:8787/oauth/callback"),
    ):
        raise RuntimeError("boom")

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_http_oauth_provider_endpoint_is_rejected(tmp_path):
    secret = tmp_path / "client.json"
    secret.write_text(
        '{"installed":{"auth_uri":"http://accounts.example/auth",'
        '"token_uri":"https://oauth2.example/token"}}'
    )

    with pytest.raises(ValueError, match="auth_uri"):
        _validate_oauth_client_endpoints(secret)


def test_http_oauth_token_endpoint_is_rejected(tmp_path):
    secret = tmp_path / "client.json"
    secret.write_text(
        '{"installed":{"auth_uri":"https://accounts.example/auth",'
        '"token_uri":"http://oauth2.example/token"}}'
    )

    with pytest.raises(ValueError, match="token_uri"):
        _validate_oauth_client_endpoints(secret)


def test_oauth_provider_endpoint_requires_hostname(tmp_path):
    secret = tmp_path / "client.json"
    secret.write_text(
        '{"installed":{"auth_uri":"https:///auth","token_uri":"https://oauth2.example/token"}}'
    )

    with pytest.raises(ValueError, match="auth_uri"):
        _validate_oauth_client_endpoints(secret)


def test_https_oauth_provider_endpoints_are_accepted(tmp_path):
    secret = tmp_path / "client.json"
    secret.write_text(
        '{"installed":{"auth_uri":"https://accounts.example/auth",'
        '"token_uri":"https://oauth2.example/token"}}'
    )

    _validate_oauth_client_endpoints(secret)

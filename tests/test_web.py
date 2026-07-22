from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import email_workflows.auth as auth
from email_workflows.store import Store
from email_workflows.web import create_app


def test_dashboard_creates_and_lists_rule(tmp_path: Path):
    store = Store(tmp_path / "app.db")
    client = TestClient(create_app(store=store, start_worker=False))

    response = client.post(
        "/rules",
        data={
            "name": "Invoices",
            "gmail_query": "from:billing@example.com is:unread",
            "prompt_template": "Summarize ${subject}",
            "priority": "10",
            "timeout_seconds": "120",
            "enabled": "on",
            "toolsets": "",
            "skills": "",
            "account_email": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = client.get("/")
    assert page.status_code == 200
    assert "Invoices" in page.text
    assert "from:billing@example.com is:unread" in page.text


def test_updating_a_missing_rule_returns_404(tmp_path: Path):
    client = TestClient(create_app(store=Store(tmp_path / "app.db"), start_worker=False))
    response = client.post(
        "/rules/999",
        data={
            "name": "Ghost",
            "gmail_query": "from:a",
            "prompt_template": "x",
            "priority": "10",
            "timeout_seconds": "120",
        },
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_health_endpoint_reports_configuration_state(tmp_path: Path):
    app = create_app(store=Store(tmp_path / "app.db"), start_worker=False)
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["oauth_configured"] is False


def test_cross_origin_rule_creation_is_rejected(tmp_path: Path):
    client = TestClient(create_app(Store(tmp_path / "app.db"), start_worker=False))
    response = client.post(
        "/rules",
        headers={"Origin": "https://attacker.example"},
        data={"name": "bad", "gmail_query": "from:a", "prompt_template": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_different_localhost_port_is_cross_origin(tmp_path: Path):
    client = TestClient(create_app(Store(tmp_path / "app.db"), start_worker=False))
    response = client.post(
        "/rules",
        headers={"Origin": "http://testserver:9999"},
        data={"name": "bad", "gmail_query": "from:a", "prompt_template": "x"},
    )
    assert response.status_code == 403


def test_different_scheme_is_cross_origin(tmp_path: Path):
    client = TestClient(create_app(Store(tmp_path / "app.db"), start_worker=False))
    response = client.post(
        "/rules",
        headers={"Origin": "https://testserver"},
        data={"name": "bad", "gmail_query": "from:a", "prompt_template": "x"},
    )
    assert response.status_code == 403


def test_untrusted_host_is_rejected(tmp_path: Path):
    client = TestClient(create_app(Store(tmp_path / "app.db"), start_worker=False))
    assert client.get("http://attacker.example/").status_code == 400


def test_oauth_redirect_uses_configured_port(tmp_path: Path, monkeypatch):
    secret = tmp_path / "client.json"
    secret.write_text('{"installed":{"project_id":"my-project"}}')
    monkeypatch.setenv("HEW_PORT", "9999")
    monkeypatch.setattr(
        auth,
        "begin_oauth",
        lambda client_secret_path, redirect_uri: ("https://accounts.example/auth", "s", "v"),
    )
    store = Store(tmp_path / "app.db")
    client = TestClient(create_app(store, start_worker=False))
    response = client.post(
        "/settings",
        data={
            "project_id": "my-project",
            "client_secret_path": str(secret),
            "topic_id": "gmail-events",
            "subscription_id": "gmail-events-local",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert store.get_setting("oauth_redirect_uri") == "http://127.0.0.1:9999/oauth/callback"


def test_rule_input_validation_rejects_blank_fields(tmp_path: Path):
    client = TestClient(create_app(Store(tmp_path / "app.db"), start_worker=False))
    response = client.post(
        "/rules",
        data={"name": " ", "gmail_query": " ", "prompt_template": " ", "timeout_seconds": "0"},
    )
    assert response.status_code == 400

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

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


def test_health_endpoint_reports_configuration_state(tmp_path: Path):
    app = create_app(store=Store(tmp_path / "app.db"), start_worker=False)
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["oauth_configured"] is False

"""FastAPI dashboard and rule-management routes."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse

from .models import Rule
from .notifier import TELEGRAM_DESTINATION
from .store import Store

PACKAGE_DIR = Path(__file__).parent
DEFAULT_DATA_DIR = Path(
    os.environ.get("HEW_DATA_DIR", "~/.local/share/hermes-email-workflows")
).expanduser()
RESOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{2,254}$")


def _validate_rule(name: str, gmail_query: str, prompt_template: str, timeout: int) -> None:
    if not name.strip() or not gmail_query.strip() or not prompt_template.strip():
        raise HTTPException(400, "Name, Gmail query, and prompt are required")
    if not 1 <= timeout <= 1800:
        raise HTTPException(400, "Timeout must be between 1 and 1800 seconds")


def _validate_destination(destination: str) -> str:
    destination = destination.strip() or "telegram"
    if not TELEGRAM_DESTINATION.fullmatch(destination):
        raise HTTPException(
            400,
            "Destination must be telegram, telegram:<chat_id>, or telegram:<chat_id>:<thread_id>",
        )
    return destination


def create_app(store: Store | None = None, start_worker: bool = True) -> FastAPI:
    store = store or Store(DEFAULT_DATA_DIR / "workflows.db")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            from .worker import WorkflowWorker

            worker = WorkflowWorker.from_store(store)
            worker.start_if_configured()
            app.state.worker = worker
        yield
        # Read the live worker off app.state: the OAuth callback may have replaced the
        # startup worker with a freshly configured one, and that is the instance holding
        # the open subscriber and background threads that must be stopped.
        worker = getattr(app.state, "worker", None)
        if worker:
            worker.stop()

    app = FastAPI(title="Hermes Email Workflows", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.middleware("http")
    async def reject_cross_origin_mutations(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return PlainTextResponse("Cross-origin request rejected", status_code=403)
            source = request.headers.get("origin") or request.headers.get("referer")
            if source:
                parsed = urlsplit(source)
                request_host = request.headers.get("host", "").lower()
                if parsed.scheme != request.url.scheme or parsed.netloc.lower() != request_host:
                    return PlainTextResponse("Cross-origin request rejected", status_code=403)
        return await call_next(request)

    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")
    app.state.store = store

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        settings = store.all_settings()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "rules": store.list_rules(),
                "events": store.list_events(),
                "settings": settings,
                "oauth_configured": Path(settings.get("token_path", "")).expanduser().exists()
                if settings.get("token_path")
                else False,
            },
        )

    @app.get("/rules/new", response_class=HTMLResponse)
    def new_rule(request: Request):
        return templates.TemplateResponse(request, "rule_form.html", {"rule": None})

    @app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
    def edit_rule(request: Request, rule_id: int):
        rule = store.get_rule(rule_id)
        if not rule:
            raise HTTPException(404, "Rule not found")
        return templates.TemplateResponse(request, "rule_form.html", {"rule": rule})

    @app.post("/rules")
    def create_rule(
        name: str = Form(...),
        gmail_query: str = Form(...),
        prompt_template: str = Form(...),
        enabled: str | None = Form(None),
        priority: int = Form(100),
        account_email: str = Form(""),
        toolsets: str = Form("web"),
        skills: str = Form(""),
        timeout_seconds: int = Form(300),
        destination: str = Form("telegram"),
    ):
        _validate_rule(name, gmail_query, prompt_template, timeout_seconds)
        store.create_rule(
            Rule(
                None,
                name.strip(),
                gmail_query.strip(),
                prompt_template,
                enabled=enabled == "on",
                priority=priority,
                account_email=account_email.strip() or None,
                toolsets=toolsets.strip() or "web",
                skills=skills.strip(),
                timeout_seconds=timeout_seconds,
                destination=_validate_destination(destination),
            )
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/rules/{rule_id}")
    def update_rule(
        rule_id: int,
        name: str = Form(...),
        gmail_query: str = Form(...),
        prompt_template: str = Form(...),
        enabled: str | None = Form(None),
        priority: int = Form(100),
        account_email: str = Form(""),
        toolsets: str = Form("web"),
        skills: str = Form(""),
        timeout_seconds: int = Form(300),
        destination: str = Form("telegram"),
    ):
        _validate_rule(name, gmail_query, prompt_template, timeout_seconds)
        try:
            store.update_rule(
                Rule(
                    rule_id,
                    name.strip(),
                    gmail_query.strip(),
                    prompt_template,
                    enabled=enabled == "on",
                    priority=priority,
                    account_email=account_email.strip() or None,
                    toolsets=toolsets.strip() or "web",
                    skills=skills.strip(),
                    timeout_seconds=timeout_seconds,
                    destination=_validate_destination(destination),
                )
            )
        except KeyError as exc:
            raise HTTPException(404, "Rule not found") from exc
        return RedirectResponse("/", status_code=303)

    @app.post("/rules/{rule_id}/delete")
    def delete_rule(rule_id: int):
        store.delete_rule(rule_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html", {"settings": store.all_settings()}
        )

    @app.post("/settings")
    def save_settings(
        project_id: str = Form(...),
        client_secret_path: str = Form(...),
        topic_id: str = Form("hermes-email-events"),
        subscription_id: str = Form("hermes-email-workflows-local"),
        preprovisioned: str | None = Form(None),
    ):
        from .auth import begin_oauth, oauth_client_project

        if not RESOURCE_ID.fullmatch(project_id.strip()):
            raise HTTPException(400, "Invalid Google Cloud project ID")
        if not RESOURCE_ID.fullmatch(topic_id.strip()) or not RESOURCE_ID.fullmatch(
            subscription_id.strip()
        ):
            raise HTTPException(400, "Invalid Pub/Sub topic or subscription ID")

        client_secret_path = str(Path(client_secret_path).expanduser())
        if not Path(client_secret_path).exists():
            raise HTTPException(400, "OAuth client JSON does not exist")
        client_project = oauth_client_project(client_secret_path)
        if client_project and project_id.strip() != client_project:
            raise HTTPException(
                400,
                "Gmail watch requires the Pub/Sub topic to use the OAuth client's "
                f"project: {client_project}",
            )
        redirect_uri = f"http://127.0.0.1:{int(os.environ.get('HEW_PORT', '8787'))}/oauth/callback"
        auth_url, state, verifier = begin_oauth(client_secret_path, redirect_uri)
        for key, value in {
            "project_id": project_id.strip(),
            "client_secret_path": client_secret_path,
            "topic_id": topic_id.strip(),
            "subscription_id": subscription_id.strip(),
            "pubsub_mode": "preprovisioned" if preprovisioned == "on" else "provision",
            "oauth_state": state,
            "oauth_code_verifier": verifier,
            "oauth_redirect_uri": redirect_uri,
        }.items():
            store.set_setting(key, value)
        return RedirectResponse(auth_url, status_code=303)

    @app.get("/oauth/callback")
    def oauth_callback(request: Request, state: str):
        from .auth import build_services, finish_oauth
        from .gmail import GmailClient
        from .google_setup import GoogleSetup

        expected_state = store.get_setting("oauth_state", "") or ""
        if not expected_state or state != expected_state:
            raise HTTPException(400, "OAuth state did not match")
        data_dir = Path(os.environ.get("HEW_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
        token_path = data_dir / "google-token.json"
        credentials = finish_oauth(
            store.get_setting("client_secret_path", "") or "",
            store.get_setting("oauth_redirect_uri", "") or "",
            expected_state,
            store.get_setting("oauth_code_verifier", "") or "",
            str(request.url),
            token_path,
        )
        gmail_service, pubsub_service = build_services(credentials)
        resources = GoogleSetup(pubsub_service).ensure_pubsub(
            store.get_setting("project_id", "") or "",
            store.get_setting("topic_id", "hermes-email-events") or "hermes-email-events",
            store.get_setting("subscription_id", "hermes-email-workflows-local")
            or "hermes-email-workflows-local",
            preprovisioned=store.get_setting("pubsub_mode", "") == "preprovisioned",
        )
        gmail = GmailClient(gmail_service)
        gmail.ensure_processed_label()
        profile = gmail.profile()
        account_email = profile["emailAddress"]
        # Establish a baseline so reconnecting does not treat the existing backlog as
        # newly delivered mail during a later stale-history reconciliation. Stamp the
        # processed label too, so safety sweeps stop listing baseline messages.
        for message_id in gmail.unprocessed_inbox_message_ids():
            if store.claim_message(account_email, message_id):
                store.finish_message(account_email, message_id, "baseline_ignored", [], "")
            gmail.add_processed_label(message_id)
        watch = gmail.start_watch(resources.topic)
        for key, value in {
            "token_path": str(token_path),
            "account_email": account_email,
            "history_id": str(watch["historyId"]),
            "watch_expiration": str(watch["expiration"]),
            "topic_path": resources.topic,
            "subscription_path": resources.subscription,
            "worker_status": "starting",
            "oauth_state": "",
            "oauth_code_verifier": "",
        }.items():
            store.set_setting(key, value)
        if start_worker:
            from .worker import WorkflowWorker

            current_worker = getattr(request.app.state, "worker", None)
            if current_worker:
                current_worker.stop()
            worker = WorkflowWorker.from_store(store)
            worker.start_if_configured()
            request.app.state.worker = worker
        return RedirectResponse("/?connected=1", status_code=303)

    @app.get("/healthz")
    def health():
        token_path = store.get_setting("token_path", "") or ""
        return {
            "status": "ok",
            "oauth_configured": bool(token_path and Path(token_path).expanduser().exists()),
            "active_account": store.get_setting("account_email", ""),
            "watch_expiration": store.get_setting("watch_expiration", ""),
        }

    return app

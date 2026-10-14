"""Dashboard routes: queue, calendar, composer, logs, health."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta

from fastapi import APIRouter, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from autoposter.config.settings import AppSettings
from autoposter.core import queue as q
from autoposter.core.errors import ConfigError
from autoposter.core.models import PostStatus
from autoposter.utils.timeutil import utcnow

MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25MB

GetSession = Callable[[], AbstractContextManager[Session]]
GetCsrfToken = Callable[[], str]
VerifyCsrf = Callable[[str | None], bool]


def register_routes(
    app: FastAPI,
    *,
    templates: Jinja2Templates,
    settings: AppSettings,
    get_session: GetSession,
    get_csrf_token: GetCsrfToken,
    verify_csrf: VerifyCsrf,
    password: str | None,
) -> None:
    """Register all dashboard routes."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def queue_view(
        request: Request,
        status: str | None = Query(None),
        platform: str | None = Query(None),
    ) -> HTMLResponse:
        post_status = None
        if status:
            try:
                post_status = PostStatus(status)
            except ValueError:
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    {"message": f"Invalid status: {status}"},
                    status_code=400,
                )
        with get_session() as session:
            posts = q.list_posts(session, status=post_status, platform=platform, limit=200)
        csrf = get_csrf_token()
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "request": request,
                "posts": posts,
                "status_filter": status,
                "platform_filter": platform,
                "csrf_token": csrf,
                "now": utcnow(),
            },
        )

    @router.get("/calendar", response_class=HTMLResponse)
    async def calendar_view(request: Request) -> HTMLResponse:
        now = utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with get_session() as session:
            posts = q.list_posts(
                session,
                scheduled_after=start,
                scheduled_before=start + timedelta(days=42),
                limit=500,
            )
        days = []
        for i in range(42):
            day_date = start + timedelta(days=i)
            day_posts = [p for p in posts if p.scheduled_at_utc.date() == day_date.date()]
            days.append({"date": day_date, "posts": day_posts})
        return templates.TemplateResponse(
            request,
            "calendar.html",
            {"request": request, "days": days, "now": now},
        )

    @router.get("/composer", response_class=HTMLResponse)
    async def composer_view(request: Request) -> HTMLResponse:
        csrf = get_csrf_token()
        return templates.TemplateResponse(
            request,
            "composer.html",
            {
                "request": request,
                "csrf_token": csrf,
                "default_timezone": settings.config.default_timezone,
            },
        )

    @router.post("/posts", response_class=RedirectResponse)
    async def create_post(
        request: Request,
        body: str = Form(...),
        platforms: str = Form(...),
        datetime_str: str = Form(...),
        timezone: str = Form("UTC"),
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        if not verify_csrf(csrf_token):
            return RedirectResponse("/composer?error=csrf", status_code=302)

        try:
            scheduled = _parse_datetime(datetime_str, timezone)
        except ConfigError:
            return RedirectResponse("/composer?error=invalid_datetime", status_code=302)

        platform_list = [p.strip() for p in platforms.split(",") if p.strip()]
        targets = []
        with get_session() as session:
            from autoposter.auth.crypto import resolve_encryption_key
            from autoposter.auth.tokens import TokenStore

            key = resolve_encryption_key(
                settings.secrets.encryption_key, settings.paths.data_dir
            )
            store = TokenStore(key)
            for platform in platform_list:
                account = store.get_account(session, platform)
                if account:
                    targets.append(q.TargetSpec(platform=platform, account_id=account.id))

            if targets:
                q.add_post(
                    session,
                    body=body,
                    scheduled_at_utc=scheduled,
                    timezone=timezone,
                    targets=targets,
                )

        return RedirectResponse("/?created=1", status_code=302)

    @router.post("/posts/{post_id}/cancel", response_class=RedirectResponse)
    async def cancel_post(post_id: int, csrf_token: str = Form(...)) -> RedirectResponse:
        if not verify_csrf(csrf_token):
            return RedirectResponse("/?error=csrf", status_code=302)
        with get_session() as session:
            q.set_post_status(session, post_id, PostStatus.CANCELLED)
        return RedirectResponse("/?cancelled=1", status_code=302)

    @router.post("/posts/{post_id}/pause", response_class=RedirectResponse)
    async def pause_post(post_id: int, csrf_token: str = Form(...)) -> RedirectResponse:
        if not verify_csrf(csrf_token):
            return RedirectResponse("/?error=csrf", status_code=302)
        with get_session() as session:
            q.set_post_status(session, post_id, PostStatus.PAUSED)
        return RedirectResponse("/?paused=1", status_code=302)

    @router.post("/posts/{post_id}/resume", response_class=RedirectResponse)
    async def resume_post(post_id: int, csrf_token: str = Form(...)) -> RedirectResponse:
        if not verify_csrf(csrf_token):
            return RedirectResponse("/?error=csrf", status_code=302)
        with get_session() as session:
            q.set_post_status(session, post_id, PostStatus.SCHEDULED)
        return RedirectResponse("/?resumed=1", status_code=302)

    @router.get("/logs", response_class=HTMLResponse)
    async def logs_view(request: Request, lines: int = Query(100)) -> HTMLResponse:
        log_file = settings.paths.log_dir / "autoposter.log"
        log_lines: list[str] = []
        if log_file.exists():
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_lines = all_lines[-lines:]
        return templates.TemplateResponse(
            request,
            "logs.html",
            {"request": request, "log_lines": log_lines},
        )

    @router.get("/health", response_class=HTMLResponse)
    async def health_view(request: Request) -> HTMLResponse:
        from autoposter.auth.crypto import resolve_encryption_key
        from autoposter.auth.tokens import TokenStore

        key = resolve_encryption_key(
            settings.secrets.encryption_key, settings.paths.data_dir
        )
        store = TokenStore(key)
        accounts: list = []
        with get_session() as session:
            accounts = store.list_accounts(session)
        return templates.TemplateResponse(
            request,
            "health.html",
            {"request": request, "accounts": accounts},
        )

    @router.get("/login")
    async def login_view(request: Request):
        if not password:
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request},
        )

    @router.post("/login", response_class=RedirectResponse)
    async def login_submit(
        request: Request,
        password_attempt: str = Form(...),
    ) -> RedirectResponse:
        if password and _verify_password(password_attempt, _hash_password(password)):
            response = RedirectResponse("/", status_code=302)
            response.set_cookie(
                "autoposter_session",
                _hash_password(password),
                httponly=True,
                samesite="lax",
            )
            return response
        return RedirectResponse("/login?error=1", status_code=302)

    app.include_router(router)


def _parse_datetime(dt_str: str, timezone: str) -> datetime:
    """Parse a user datetime string."""
    from autoposter.utils.timeutil import parse_user_datetime

    return parse_user_datetime(dt_str, timezone)


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b, strict=True):
        result |= ord(x) ^ ord(y)
    return result == 0


def _hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256 with salt for session cookie comparison."""
    if salt is None:
        salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()
    return f"{salt}${derived}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored PBKDF2 hash."""
    if "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return _constant_time_compare(_hash_password(password, salt), stored)

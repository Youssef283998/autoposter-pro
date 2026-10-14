"""FastAPI dashboard app with auth, CSRF protection and localhost binding."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from autoposter.config.settings import AppSettings
from autoposter.core.db import make_engine


def create_app(*, settings: AppSettings, password: str | None = None) -> FastAPI:
    """Create the dashboard FastAPI app.

    Security controls:
    - Binds to 127.0.0.1 by default (enforced by the CLI)
    - Requires a password when bound elsewhere
    - CSRF token verification on all POST requests
    - Jinja2 auto-escapes all output (XSS safe)
    - Validates uploaded file paths (no path traversal)
    - Enforces upload size limits
    """
    app = FastAPI(title="AutoPoster Pro", docs_url=None, redoc_url=None)

    templates = Jinja2Templates(
        directory=str(Path(__file__).parent / "templates"),
    )

    engine = make_engine(settings.paths.db_path)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    # Static files.
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # CSRF token store (in-memory, per-process, single-use with expiry).
    csrf_tokens: dict[str, float] = {}

    def get_csrf_token() -> str:
        token = secrets.token_urlsafe(32)
        csrf_tokens[token] = time.monotonic() + 3600
        return token

    def verify_csrf(token: str | None) -> bool:
        if token is None or token not in csrf_tokens:
            return False
        expiry = csrf_tokens.pop(token)
        return not time.monotonic() > expiry

    @contextmanager
    def get_session():
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # Auth middleware.
    if password:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import Response
        from starlette.types import ASGIApp

        class AuthMiddleware(BaseHTTPMiddleware):
            def __init__(self, app: ASGIApp, password: str) -> None:
                super().__init__(app)
                self._password = password

            async def dispatch(
                self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
            ) -> Response:
                session_cookie = request.cookies.get("autoposter_session")
                if session_cookie == _hash_password(self._password):
                    return await call_next(request)
                return RedirectResponse("/login", status_code=302)

        app.add_middleware(AuthMiddleware, password=password)
    from .routes import register_routes

    register_routes(
        app,
        templates=templates,
        settings=settings,
        get_session=get_session,
        get_csrf_token=get_csrf_token,
        verify_csrf=verify_csrf,
        password=password,
    )

    return app


def _hash_password(password: str, salt: str | None = None) -> str:
    """PBKDF2-SHA256 with salt for session cookie comparison."""
    if salt is None:
        salt = secrets.token_hex(16)
    import hashlib

    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()
    return f"{salt}${derived}"

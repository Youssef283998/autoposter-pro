"""OAuth 2.0 PKCE flow for X and LinkedIn.

Supports both a local-callback server (opens browser, receives redirect) and
a copy-paste-code flow (prints URL, user pastes the code). Tokens are stored
encrypted via TokenStore.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from autoposter.core.errors import AuthError
from autoposter.utils.logging import get_logger

UTC = timezone.utc

logger = get_logger("auth.oauth")

DEFAULT_REDIRECT_PORT = 8765
DEFAULT_REDIRECT_URI = f"http://127.0.0.1:{DEFAULT_REDIRECT_PORT}/callback"


@dataclass(frozen=True)
class PKCEData:
    """PKCE verifier, challenge and state for one OAuth flow."""

    verifier: str
    challenge: str
    state: str


@dataclass
class OAuthToken:
    """OAuth token response."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    token_type: str = "Bearer"
    scope: str = ""

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at - timedelta(minutes=5)

    def to_secrets(self) -> dict[str, str]:
        """Convert to a secrets dict for TokenStore."""
        out = {
            "access_token": self.access_token,
            "token_type": self.token_type,
        }
        if self.refresh_token:
            out["refresh_token"] = self.refresh_token
        if self.expires_at:
            out["expires_at"] = self.expires_at.isoformat()
        if self.scope:
            out["scope"] = self.scope
        return out

    @classmethod
    def from_secrets(cls, secrets: dict[str, str]) -> OAuthToken:
        """Build from a secrets dict."""
        expires_at = None
        if "expires_at" in secrets:
            try:
                expires_at = datetime.fromisoformat(secrets["expires_at"])
            except ValueError:
                expires_at = None
        return cls(
            access_token=secrets.get("access_token", ""),
            refresh_token=secrets.get("refresh_token"),
            expires_at=expires_at,
            token_type=secrets.get("token_type", "Bearer"),
            scope=secrets.get("scope", ""),
        )


def generate_pkce() -> PKCEData:
    """Generate PKCE verifier, challenge and state."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(16)
    return PKCEData(verifier=verifier, challenge=challenge, state=state)


def build_auth_url(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
    code_challenge: str,
    base_url: str,
) -> str:
    """Build the OAuth 2.0 authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"


async def exchange_code(
    *,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    token_url: str,
) -> OAuthToken:
    """Exchange an authorization code for tokens."""
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            return _parse_token_response(resp.json())
    except httpx.HTTPError as exc:
        raise AuthError(
            f"Token exchange failed: {exc}",
            fix="Check your network connection and try again.",
        ) from exc


async def refresh_access_token(
    *,
    client_id: str,
    client_secret: str | None,
    refresh_token: str,
    token_url: str,
) -> OAuthToken:
    """Refresh an access token."""
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            return _parse_token_response(resp.json())
    except httpx.HTTPError as exc:
        raise AuthError(
            f"Token refresh failed: {exc}",
            fix="Re-run the OAuth flow to re-authorize.",
        ) from exc


def _parse_token_response(data: dict[str, object]) -> OAuthToken:
    """Parse a token endpoint response."""
    access_token = str(data.get("access_token", ""))
    if not access_token:
        raise AuthError(
            "Token response missing access_token.",
            fix="Re-run the OAuth flow.",
        )
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = datetime.now(UTC) + timedelta(seconds=int(str(expires_in)))
    return OAuthToken(
        access_token=access_token,
        refresh_token=str(refresh_token) if refresh_token else None,
        expires_at=expires_at,
        token_type=str(data.get("token_type", "Bearer")),
        scope=str(data.get("scope", "")),
    )


class CallbackServer:
    """Local HTTP server that receives the OAuth redirect."""

    def __init__(self, port: int = DEFAULT_REDIRECT_PORT) -> None:
        self._port = port
        self._code: str | None = None
        self._state: str | None = None
        self._error: str | None = None
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the callback server in a background thread."""
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    outer._code = params["code"][0]
                    outer._state = params.get("state", [None])[0]
                if "error" in params:
                    outer._error = params["error"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>AutoPoster</h1>"
                    b"<p>You can close this window.</p></body></html>"
                )

            def log_message(self, *args: object) -> None:
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def wait_for_code(self, *, timeout: float = 120.0) -> tuple[str, str | None]:
        """Wait for the callback. Returns (code, state)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._error:
                raise AuthError(
                    f"OAuth error: {self._error}",
                    fix="Re-run the OAuth flow and authorize access.",
                )
            if self._code:
                return self._code, self._state
            time.sleep(0.2)
        raise AuthError(
            "OAuth callback timed out.",
            fix="Re-run the OAuth flow and complete authorization in the browser.",
        )

    def stop(self) -> None:
        """Stop the callback server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


async def run_oauth_flow(
    *,
    client_id: str,
    client_secret: str | None,
    scopes: list[str],
    auth_url_base: str,
    token_url: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    port: int = DEFAULT_REDIRECT_PORT,
    open_browser: bool = True,
) -> OAuthToken:
    """Run the full OAuth 2.0 PKCE flow with a local callback server.

    Opens the browser (or prints the URL), waits for the redirect, exchanges
    the code for tokens.
    """
    pkce = generate_pkce()
    auth_url = build_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=pkce.state,
        code_challenge=pkce.challenge,
        base_url=auth_url_base,
    )

    server = CallbackServer(port=port)
    server.start()
    try:
        if open_browser:
            webbrowser.open(auth_url)
        else:
            print(f"Open this URL in your browser:\n  {auth_url}")

        code, state = server.wait_for_code()
        if state != pkce.state:
            raise AuthError(
                "OAuth state mismatch (possible CSRF).",
                fix="Re-run the OAuth flow.",
            )
        return await exchange_code(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=pkce.verifier,
            token_url=token_url,
        )
    finally:
        server.stop()


async def run_oauth_flow_paste(
    *,
    client_id: str,
    client_secret: str | None,
    scopes: list[str],
    auth_url_base: str,
    token_url: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> OAuthToken:
    """Run OAuth 2.0 PKCE with a copy-paste-code flow (no local server)."""
    pkce = generate_pkce()
    auth_url = build_auth_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=pkce.state,
        code_challenge=pkce.challenge,
        base_url=auth_url_base,
    )
    print(f"Open this URL in your browser:\n  {auth_url}")
    print("After authorizing, paste the code from the redirect URL:")
    code = input("Code: ").strip()
    if not code:
        raise AuthError(
            "No authorization code provided.",
            fix="Re-run the OAuth flow and paste the code.",
        )
    return await exchange_code(
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=pkce.verifier,
        token_url=token_url,
    )

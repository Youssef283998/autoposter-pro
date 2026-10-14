"""OAuth PKCE and token flow tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import respx

from autoposter.auth.oauth import (
    OAuthToken,
    build_auth_url,
    exchange_code,
    generate_pkce,
    refresh_access_token,
)


def test_generate_pkce_unique() -> None:
    a = generate_pkce()
    b = generate_pkce()
    assert a.verifier != b.verifier
    assert a.challenge != b.challenge
    assert a.state != b.state


def test_build_auth_url_contains_params() -> None:
    url = build_auth_url(
        client_id="cid",
        redirect_uri="http://127.0.0.1:8765/callback",
        scopes=["tweet.read", "tweet.write"],
        state="st",
        code_challenge="cc",
        base_url="https://example.com/authorize",
    )
    assert "client_id=cid" in url
    assert "state=st" in url
    assert "code_challenge=cc" in url
    assert "tweet.read" in url


def test_oAuthToken_expiry() -> None:
    token = OAuthToken(access_token="x", expires_at=datetime.now(UTC) + timedelta(hours=1))
    assert not token.is_expired
    token2 = OAuthToken(access_token="x", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert token2.is_expired


def test_oAuthToken_secrets_roundtrip() -> None:
    token = OAuthToken(
        access_token="at",
        refresh_token="rt",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scope="tweet.read",
    )
    secrets = token.to_secrets()
    assert secrets["access_token"] == "at"
    assert secrets["refresh_token"] == "rt"
    restored = OAuthToken.from_secrets(secrets)
    assert restored.access_token == "at"
    assert restored.refresh_token == "rt"
    assert restored.expires_at is not None


@respx.mock
async def test_exchange_code_success() -> None:
    route = respx.post("https://example.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-at",
                "refresh_token": "new-rt",
                "expires_in": 7200,
                "token_type": "Bearer",
            },
        )
    )
    token = await exchange_code(
        client_id="cid",
        client_secret=None,
        code="code123",
        redirect_uri="http://127.0.0.1:8765/callback",
        code_verifier="verifier",
        token_url="https://example.com/token",
    )
    assert token.access_token == "new-at"
    assert token.refresh_token == "new-rt"
    assert token.expires_at is not None
    assert route.called


@respx.mock
async def test_refresh_access_token_success() -> None:
    route = respx.post("https://example.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "refreshed-at",
                "refresh_token": "refreshed-rt",
                "expires_in": 7200,
            },
        )
    )
    token = await refresh_access_token(
        client_id="cid",
        client_secret="secret",
        refresh_token="old-rt",
        token_url="https://example.com/token",
    )
    assert token.access_token == "refreshed-at"
    assert route.called

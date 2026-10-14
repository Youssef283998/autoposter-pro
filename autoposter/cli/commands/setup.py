"""autoposter setup <platform> — guided credential entry / OAuth flow."""

from __future__ import annotations

from pathlib import Path

import typer

from autoposter.auth.tokens import TokenStore
from autoposter.cli.rich_helpers import print_error, print_info, print_success
from autoposter.core.db import session_scope
from autoposter.core.errors import ConfigError

PLATFORM_SCOPES = {
    "x": ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"],
    "linkedin": ["w_member_social", "r_liteprofile"],
}

AUTH_URLS = {
    "x": "https://twitter.com/i/oauth2/authorize",
    "linkedin": "https://www.linkedin.com/oauth/v2/authorization",
}

TOKEN_URLS = {
    "x": "https://api.twitter.com/2/oauth2/token",
    "linkedin": "https://www.linkedin.com/oauth/v2/accessToken",
}

REDIRECT_URIS = {
    "x": "http://127.0.0.1:8765/callback",
    "linkedin": "http://127.0.0.1:8765/callback",
}


def setup_command(
    platform: str,
    *,
    config_path: Path = Path("config.yaml"),
    account_label: str = "default",
    non_interactive: bool = False,
) -> None:
    """Guided setup for one platform."""
    from autoposter.config.settings import load_settings
    from autoposter.core.db import init_database, make_engine

    settings = load_settings(config_path)
    paths = settings.paths
    paths.ensure()

    engine = make_engine(paths.db_path)
    init_database(engine)

    store = TokenStore(resolve_key(settings))

    if platform in ("x", "linkedin"):
        _setup_oauth(platform, store, engine, settings, non_interactive)
    elif platform == "telegram":
        _setup_telegram(store, engine, non_interactive)
    elif platform == "discord":
        _setup_discord(store, engine, non_interactive)
    elif platform == "mastodon":
        _setup_mastodon(store, engine, settings, non_interactive)
    elif platform == "bluesky":
        _setup_bluesky(store, engine, non_interactive)
    else:
        print_error(ConfigError(f"Unknown platform: {platform}"))
        raise typer.Exit(1)

    engine.dispose()


def resolve_key(settings) -> bytes:
    from autoposter.auth.crypto import resolve_encryption_key

    return resolve_encryption_key(settings.secrets.encryption_key, settings.paths.data_dir)


def _setup_oauth(
    platform: str,
    store: TokenStore,
    engine,
    settings,
    non_interactive: bool,
) -> None:
    """OAuth 2.0 PKCE flow for X and LinkedIn."""
    import asyncio

    from autoposter.auth.oauth import run_oauth_flow, run_oauth_flow_paste

    client_id = _prompt(f"{platform} client ID", non_interactive)
    client_secret = _prompt(f"{platform} client secret (optional)", non_interactive, default="")

    print_info("Starting OAuth flow...")
    print_info("A browser window will open. Authorize AutoPoster and paste the code.")

    try:
        if non_interactive:
            token = asyncio.run(
                run_oauth_flow_paste(
                    client_id=client_id,
                    client_secret=client_secret or None,
                    scopes=PLATFORM_SCOPES[platform],
                    auth_url_base=AUTH_URLS[platform],
                    token_url=TOKEN_URLS[platform],
                    redirect_uri=REDIRECT_URIS[platform],
                )
            )
        else:
            token = asyncio.run(
                run_oauth_flow(
                    client_id=client_id,
                    client_secret=client_secret or None,
                    scopes=PLATFORM_SCOPES[platform],
                    auth_url_base=AUTH_URLS[platform],
                    token_url=TOKEN_URLS[platform],
                    redirect_uri=REDIRECT_URIS[platform],
                )
            )
    except Exception as exc:
        print_error(ConfigError(f"OAuth failed: {exc}"))
        raise typer.Exit(1) from exc

    secrets = token.to_secrets()
    secrets["client_id"] = client_id
    if client_secret:
        secrets["client_secret"] = client_secret

    with session_scope(engine) as session:
        store.save_account(
            session,
            platform=platform,
            secrets=secrets,
            kind="oauth",
            token_expires_at=token.expires_at,
        )

    print_success(f"{platform} account connected")


def _setup_telegram(store: TokenStore, engine, non_interactive: bool) -> None:
    """Telegram bot token setup."""
    token = _prompt("Telegram bot token (from BotFather)", non_interactive)
    chat_id = _prompt("Chat ID (e.g. @channelname or numeric ID)", non_interactive)

    with session_scope(engine) as session:
        store.save_account(
            session,
            platform="telegram",
            secrets={"bot_token": token, "chat_id": chat_id},
            kind="bot_token",
        )

    print_success("Telegram account connected")


def _setup_discord(store: TokenStore, engine, non_interactive: bool) -> None:
    """Discord webhook or bot setup."""
    kind = typer.prompt("Discord setup type (webhook/bot)", default="webhook")
    if kind == "webhook":
        webhook_url = _prompt("Discord webhook URL", non_interactive)
        with session_scope(engine) as session:
            store.save_account(
                session,
                platform="discord",
                secrets={"webhook_url": webhook_url},
                kind="webhook",
            )
    else:
        bot_token = _prompt("Discord bot token", non_interactive)
        channel_id = _prompt("Channel ID", non_interactive)
        with session_scope(engine) as session:
            store.save_account(
                session,
                platform="discord",
                secrets={"bot_token": bot_token, "channel_id": channel_id},
                kind="bot_token",
            )

    print_success("Discord account connected")


def _setup_mastodon(store: TokenStore, engine, settings, non_interactive: bool) -> None:
    """Mastodon app token setup."""
    api_base = _prompt(
        "Mastodon instance URL",
        non_interactive,
        default=settings.secrets.mastodon_api_base or "https://mastodon.social",
    )
    client_id = _prompt("Mastodon client ID", non_interactive)
    client_secret = _prompt("Mastodon client secret", non_interactive)
    access_token = _prompt("Mastodon access token", non_interactive)

    with session_scope(engine) as session:
        store.save_account(
            session,
            platform="mastodon",
            secrets={
                "api_base": api_base,
                "client_id": client_id,
                "client_secret": client_secret,
                "access_token": access_token,
            },
            kind="manual",
        )

    print_success("Mastodon account connected")


def _setup_bluesky(store: TokenStore, engine, non_interactive: bool) -> None:
    """Bluesky app password setup."""
    handle = _prompt("Bluesky handle (e.g. user.bsky.social)", non_interactive)
    app_password = _prompt("Bluesky app password", non_interactive)

    with session_scope(engine) as session:
        store.save_account(
            session,
            platform="bluesky",
            secrets={"handle": handle, "app_password": app_password},
            kind="app_password",
        )

    print_success("Bluesky account connected")


def _prompt(
    message: str, non_interactive: bool, default: str | None = None
) -> str:
    """Prompt for input, or read from args in non-interactive mode."""
    if non_interactive:
        if default is None:
            raise ConfigError(
                f"Missing required value for {message!r} in non-interactive mode.",
                fix="Provide a default value or run without --non-interactive.",
            )
        return default
    return typer.prompt(message, default=default or "", show_default=True)

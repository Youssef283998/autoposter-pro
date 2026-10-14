# Configuration

AutoPoster uses three configuration layers:

1. **`.env`** — secrets only (tokens, keys, passwords)
2. **`config.yaml`** — behavior (timezones, retries, rate caps)
3. **CLI flags** — per-run overrides (`--dry-run`, `--config`)

## .env

Copy `.env.example` to `.env` and fill in what you need:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `ENCRYPTION_KEY` | Recommended | Fernet key for encrypting stored tokens |
| `TELEGRAM_BOT_TOKEN` | For Telegram | Bot token from BotFather |
| `DISCORD_WEBHOOK_URL` | For Discord | Webhook URL |
| `MASTODON_ACCESS_TOKEN` | For Mastodon | App access token |
| `BLUESKY_APP_PASSWORD` | For Bluesky | App password |
| `X_CLIENT_ID` | For X | OAuth client ID |
| `LINKEDIN_CLIENT_ID` | For LinkedIn | OAuth client ID |
| `SMTP_HOST` | For email alerts | SMTP server |
| `WEB_PASSWORD` | For web dashboard | Dashboard password |

## config.yaml

Copy `config.example.yaml` and adjust:

```bash
cp config.example.yaml config.yaml
```

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `default_timezone` | `UTC` | IANA timezone for posts |
| `tick_seconds` | `5` | How often the scheduler checks for due posts |
| `retry.max_attempts` | `5` | Max retry attempts per post |
| `retry.base_delay_seconds` | `2.0` | Initial backoff delay |
| `circuit_breaker.failure_threshold` | `5` | Failures before circuit opens |
| `dedup.window_hours` | `24` | Duplicate detection window |
| `missed.post_late_within_minutes` | `30` | Grace window for late posts |
| `media.max_upload_mb` | `25` | Max file size |
| `log_level` | `INFO` | Logging verbosity |

## CLI flags

```bash
autoposter run --config /path/to/config.yaml --dry-run
autoposter post add --platforms telegram --datetime "2026-10-14 09:00"
```

## Upgrade procedure

When upgrading to a new version:

```bash
# 1. Back up
autoposter backup

# 2. Update the package
pip install --upgrade autoposter

# 3. Run migrations (automatic on first run)
autoposter doctor

# 4. Verify
autoposter post list
```

Database migrations run automatically via alembic on first start after upgrade.

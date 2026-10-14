# AutoPoster Pro

**Pay once, own it.** Schedule and auto-post to X, LinkedIn, Telegram, Discord, Mastodon and Bluesky from your own machine or server — with your own API keys and no monthly fees.

Self-hosted social media auto-poster and scheduler for digital marketers, small businesses and creators. SQLite-backed queue, timezone-aware and recurring scheduling, per-platform adapters, retries with backoff, duplicate prevention, CSV/JSON bulk import, and a full CLI.

## Features

- **6 platforms**: Telegram, Discord, Mastodon, Bluesky, X, LinkedIn
- **Timezone-aware scheduling** with DST support and IANA zones
- **Recurring posts**: daily, weekly, cron/RRULE expressions
- **Per-platform text variants** with character-limit validation
- **Media upload** with MIME validation, size limits and auto-resize
- **Retries** with exponential backoff, jitter and circuit breaker
- **Duplicate prevention** via content hash within a time window
- **Crash recovery** for interrupted publishes
- **CSV/JSON bulk import** with row-level validation
- **Full CLI** with interactive and non-interactive modes
- **Optional local web dashboard** (FastAPI + Jinja2)
- **Docker support** with docker-compose
- **systemd, launchd and Windows Task Scheduler** installers
- **Telegram/SMTP/webhook alerts** with rate limiting
- **No telemetry** — fully self-hosted

## Requirements

- Python 3.10+
- Platform API credentials for the networks you want to post to

| Platform  | Difficulty | What you need                                   |
|-----------|------------|-------------------------------------------------|
| Telegram  | Easy       | Bot token from BotFather (free)                 |
| Discord   | Easy       | Webhook URL or bot token (free)                 |
| Mastodon  | Easy       | Per-instance app token (free)                   |
| Bluesky   | Easy       | Handle + app password (free)                    |
| X         | Medium     | Developer app + API plan (paid tiers vary)      |
| LinkedIn  | Hard       | Approved developer app + product permissions    |

## Quickstart

### 1. Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Initialize

```bash
autoposter init
```

This creates `config.yaml`, `.env`, the database and an encryption key.

### 3. Try the demo (no API keys needed)

```bash
autoposter demo
```

### 4. Connect a real platform

```bash
autoposter setup telegram
```

### 5. Schedule your first post

```bash
autoposter post add
```

### 6. Start the scheduler

```bash
autoposter run
```

## With Docker

```bash
docker build -t autoposter .
docker run -v $(pwd)/data:/data --env-file .env autoposter
```

Or with docker-compose:

```bash
docker-compose up -d
```

## CLI commands

```
autoposter init                  # Create config, database and encryption key
autoposter setup <platform>      # Guided credential entry / OAuth flow
autoposter doctor                # Test every connection and config
autoposter post add|list|show|edit|cancel|pause|resume|retry|reschedule|duplicate|delete
autoposter import <file>         # CSV/JSON bulk import
autoposter export                # Export queue to CSV/JSON
autoposter backup / restore      # Full backup and restore
autoposter run                   # Start the scheduler
autoposter demo                  # Demo with fake platform
autoposter web                   # Local dashboard
autoposter service install       # Background service (systemd/launchd/schtasks)
autoposter version
```

## Documentation

See `docs/` for installation, configuration, per-platform setup guides, the CLI reference, CSV format, troubleshooting, FAQ, architecture and the plugin guide.

## License

Commercial use license — see `LICENSE.txt`. Placeholder text pending legal review.

## Responsible use

You are responsible for complying with each platform's terms of service and automation rules. See `docs/responsible-use.md`.

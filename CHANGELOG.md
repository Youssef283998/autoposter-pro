# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-13

### Added

- Initial release of AutoPoster Pro
- Platform adapters: Telegram, Discord, Mastodon, Bluesky, X, LinkedIn
- SQLite-backed queue with atomic claiming
- Timezone-aware scheduling with DST support
- Recurring posts (daily, weekly, cron/RRULE)
- Per-platform variant bodies
- Media upload with validation and internal store
- Retry with exponential backoff and jitter
- Circuit breaker per platform
- Rate limiter with hourly/daily caps
- Crash recovery for interrupted publishes
- Duplicate prevention via content hash
- OAuth 2.0 PKCE flow for X and LinkedIn
- CSV/JSON bulk import with validation
- Full CLI (init, setup, doctor, post, import, export, backup, run, demo, web, service)
- Optional local web dashboard (FastAPI + Jinja2)
- Telegram/SMTP/webhook alerts with rate limiting
- systemd, launchd and Windows Task Scheduler installers
- Docker support
- CI workflow (ruff, mypy, pytest, pip-audit on 3 OS × 4 Python versions)

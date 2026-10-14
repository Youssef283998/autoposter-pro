# FAQ

## Is there a monthly fee?

No. AutoPoster Pro is a one-time purchase. You host it yourself and use your own API keys.

## Which platforms are supported at launch?

Telegram, Discord, Mastodon, Bluesky, X (Twitter) and LinkedIn. See the [platform guides](platforms/telegram.md) for setup details.

## Do I need a developer account for every platform?

No. Telegram, Discord, Mastodon and Bluesky are free and easy. X and LinkedIn require approved developer apps.

## Can I run multiple accounts per platform?

Yes. Use `--account <label>` when running `autoposter setup`. Each account gets its own credential set.

## How do I back up my queue?

```bash
autoposter backup
```

This creates a tar.gz with the database, config and media. Restore with `autoposter restore <archive>`.

## How do I upgrade?

```bash
autoposter backup
pip install --upgrade autoposter
autoposter doctor
```

Database migrations run automatically on first start.

## Can I use Docker?

Yes. See the [Dockerfile](../Dockerfile) and [docker-compose.yml](../docker-compose.yml).

## Is there a web UI?

Yes, an optional local dashboard. Run `autoposter web` and open http://127.0.0.1:8300.

## What happens if my machine is off at post time?

Posts within the 30-minute grace window are published late. Older posts are marked missed and you are notified.

## How does duplicate prevention work?

A content hash is computed for each post. If the same hash was published to the same account within 24 hours, you get a warning (or it is blocked, depending on `dedup.mode`).

## Can I schedule recurring posts?

Yes. Use `recurrence_rule` with `daily`, `weekly`, `weekly:MON@09:00`, or a full RRULE string.

## Is my data sent anywhere?

No. AutoPoster is fully self-hosted. There is no telemetry. Your credentials stay on your machine.

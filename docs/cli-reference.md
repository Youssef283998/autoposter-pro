# CLI Reference

## `autoposter init`

Create folders, config, database and encryption key.

```
autoposter init [--config PATH] [--data-dir PATH] [--force]
```

## `autoposter setup <platform>`

Guided credential entry or OAuth browser flow.

```
autoposter setup telegram
autoposter setup discord
autoposter setup mastodon
autoposter setup bluesky
autoposter setup x
autoposter setup linkedin
```

Options: `--account LABEL`, `--non-interactive`

## `autoposter doctor`

Test every connection, config, permissions, clock.

```
autoposter doctor [--platform NAME]
```

## `autoposter post <action>`

Manage posts in the queue.

```
autoposter post add [--body TEXT] [--platforms LIST] [--datetime WHEN] [--dry-run]
autoposter post list [--status STATUS] [--platform NAME] [--limit N]
autoposter post show --id ID
autoposter post edit --id ID [--body TEXT]
autoposter post cancel --id ID
autoposter post pause --id ID
autoposter post resume --id ID
autoposter post retry --id ID
autoposter post reschedule --id ID [--datetime WHEN]
autoposter post duplicate --id ID
autoposter post delete --id ID [--force]
```

## `autoposter import <file>`

Import posts from CSV or JSON.

```
autoposter import posts.csv [--validate-only] [--skip-invalid] [--dry-run]
```

## `autoposter export`

Export the queue to CSV or JSON.

```
autoposter export [--format csv|json] [--out PATH] [--status STATUS]
```

## `autoposter backup / restore`

```
autoposter backup [--out PATH]
autoposter restore <archive> [--force]
```

## `autoposter run`

Start the scheduler in the foreground.

```
autoposter run [--dry-run] [--once] [--debug]
```

## `autoposter demo`

Run the full pipeline against the fake platform.

```
autoposter demo [--posts N]
```

## `autoposter web`

Start the local web dashboard.

```
autoposter web [--host HOST] [--port PORT] [--password PASS]
```

## `autoposter service`

Install, uninstall or check the background service.

```
autoposter service install
autoposter service uninstall
autoposter service status
```

## `autoposter version`

Print the version.

# Troubleshooting

## "Config file not found"

```
Error: Config file not found: /path/to/config.yaml
  Fix: Run `autoposter init` to create config.yaml, or pass --config PATH.
```

Run `autoposter init` in the directory where you want to store your data.

## "Another AutoPoster instance is using this directory"

```
Error: Another AutoPoster instance (PID 1234) is using /data.
  Fix: Stop that instance first, or run with a different data_dir.
```

Only one scheduler can run against a database at a time. Stop the other instance or use a different `--data-dir`.

## "No account for telegram"

```
Warning: No account for telegram. Run: autoposter setup telegram
```

Run `autoposter setup <platform>` to connect the platform.

## "Circuit open for x"

```
Circuit open for x; target 42 requeued
```

The X API is returning errors consistently. The circuit breaker will retry after the cooldown period. Check `autoposter doctor` for details.

## "Auth expired for linkedin account 1"

```
Auth expired for linkedin account 1; targets paused
  Fix: Run `autoposter setup linkedin` to re-authorize.
```

OAuth tokens expired and could not be refreshed. Re-run the setup flow.

## "Max attempts (5) exceeded"

The post failed 5 times. Check the error message in `autoposter post show --id <id>` and fix the underlying issue before retrying with `autoposter post retry --id <id>`.

## "Missed schedule"

The machine was off at the scheduled time and the grace window expired. The post was marked missed. Reschedule with `autoposter post reschedule --id <id>`.

## "Ambiguous publish outcome"

A timeout occurred after the request was sent. The post was marked failed to avoid a duplicate. Check the platform manually, then retry with `autoposter post retry --id <id>`.

## "Database is locked"

```
sqlite3.OperationalError: database is locked
```

Another process is writing to the database. Ensure only one `autoposter run` instance is active. The lock file (`autoposter.lock`) prevents this — if it is stale, delete it.

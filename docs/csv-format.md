# CSV Import Format

## Columns

| Column | Required | Description |
|--------|----------|-------------|
| `text` | Yes | Post body text |
| `platforms` | Yes | Comma-separated platform names |
| `datetime` | Yes | Schedule time (YYYY-MM-DD HH:MM) |
| `timezone` | No | IANA timezone (default from config) |
| `media` | No | Semicolon-separated file paths |
| `thread_group` | No | Group ID for related posts |
| `variants` | No | Per-platform text overrides |

## Example

```csv
text,platforms,datetime,timezone,media,thread_group,variants
"Hello world",telegram,2026-10-14 09:00,UTC,,,
"Multi-platform post","telegram,discord",2026-10-14 12:00,UTC,,,
```

## Variants format

Per-platform text overrides use `platform:text|platform:text`:

```
variants=telegram:Hello Discord|discord:Hi Discord
```

## JSON format

```json
[
  {
    "text": "Hello world",
    "platforms": "telegram",
    "datetime": "2026-10-14 09:00",
    "timezone": "UTC"
  }
]
```

## Validation

The importer always runs a validation pass first:

```bash
autoposter import posts.csv --validate-only
```

Only schedules when the file is clean or `--skip-invalid` is passed.

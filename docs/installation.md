# Installation

## Requirements

- Python 3.10+
- 100 MB disk space
- Platform API credentials (see [Platforms](index.md#platforms))

## From source

```bash
git clone https://example.com/autoposter-pro.git
cd autoposter-pro
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
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

## Verify

```bash
autoposter version
autoposter doctor
```

## Next steps

1. `autoposter init` — create config and database
2. `autoposter setup telegram` — connect your first platform
3. `autoposter demo` — try the full pipeline with no API keys
4. `autoposter post add` — schedule your first post
5. `autoposter run` — start the scheduler

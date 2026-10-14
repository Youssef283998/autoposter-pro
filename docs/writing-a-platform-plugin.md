# Writing a Platform Plugin

## The adapter contract

Every platform implements the `Platform` ABC from `autoposter/platforms/base.py`:

```python
from autoposter.platforms.base import Platform
from autoposter.platforms.registry import register_platform

@register_platform
class MyPlatform(Platform):
    name = "myplatform"
    limits = PlatformLimits(
        max_chars=2000,
        max_media=4,
        allowed_mime=frozenset({"image/jpeg", "image/png"}),
        max_bytes=10 * 1024 * 1024,
    )

    def __init__(self, account, settings):
        super().__init__(account, settings)
        # Initialize your HTTP client here

    async def validate(self, post, target):
        # Return list[ValidationIssue]
        return []

    async def publish(self, post, target, idempotency_key):
        # Return PublishResult
        ...

    async def refresh_auth(self):
        # Refresh OAuth tokens
        ...

    async def healthcheck(self):
        # Return HealthStatus
        ...

    async def aclose(self):
        # Clean up resources
        ...
```

## Steps

1. Create `autoposter/platforms/myplatform.py`
2. Subclass `Platform` and decorate with `@register_platform`
3. Implement all abstract methods
4. Add platform credentials to `.env.example` and `config.example.yaml`
5. Add a setup guide in `docs/platforms/myplatform.md`
6. Add tests in `tests/integration/test_adapter_myplatform.py`

## Tips

- Use `httpx.AsyncClient` for HTTP calls
- Classify errors into `ErrorCategory` (RETRYABLE, RATE_LIMITED, AUTH_EXPIRED, PERMANENT, AMBIGUOUS)
- Honor `Retry-After` headers on 429 responses
- Use `redact_text()` on any error messages before storing them
- Set `supports_threads = True` if the platform supports reply chains
- Set `supports_alt_text = True` if the platform supports image descriptions

"""Secret redaction for logs, error messages and stored API responses.

Two mechanisms:
1. A registry of live secret *values* (encryption key, tokens loaded this
   process) replaced wherever they appear.
2. Pattern-based scrubbing for ``Authorization`` headers, ``key=value`` pairs
   and well-known token shapes (Telegram bot tokens, Discord webhooks, Fernet
   keys, X bearers).

Apply :class:`RedactingFilter` to every logging handler, and call
:func:`redact_text` before persisting any API response body.
"""

from __future__ import annotations

import logging
import re
import threading

_lock = threading.Lock()
_runtime_secrets: set[str] = set()

_MIN_SECRET_LEN = 8

# Order matters: more specific patterns first.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer xyz / Basic xyz
    (
        re.compile(r"(?i)\b(authorization\s*:\s*)(bearer|basic)\s+\S+"),
        r"\1\2 ***",
    ),
    (
        re.compile(r"(?i)\b(bearer|basic)\s+([A-Za-z0-9\-._~+/]{12,}=*)"),
        r"\1 ***",
    ),
    # key=value / key: value / "key": "value" for sensitive names
    (
        re.compile(
            r"(?i)([\"']?(?:token|secret|password|passwd|api[_-]?key|client[_-]?secret|"
            r"access[_-]?token|refresh[_-]?token|bot[_-]?token|session[_-]?id|cookie|"
            r"private[_-]?key|encryption[_-]?key|app[_-]?password)[\"']?\s*[=:]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
        ),
        r"\1***",
    ),
    # Telegram bot token: 123456789:AA...
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "***"),
    # Discord webhook URL
    (
        re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+"),
        "https://discord.com/api/webhooks/***",
    ),
    # Fernet key
    (re.compile(r"\bgAAAAA[A-Za-z0-9_\-]{20,}"), "***"),
    # X/Twitter app-only bearer (starts with a long run of A)
    (re.compile(r"\bAAAAAAAAAA[A-Za-z0-9%]{20,}"), "***"),
]


def register_secret(value: str | None) -> None:
    """Register a live secret value so it is scrubbed wherever it appears."""
    if not value:
        return
    candidate = value.strip()
    if len(candidate) < _MIN_SECRET_LEN:
        return
    with _lock:
        _runtime_secrets.add(candidate)


def unregister_secret(value: str | None) -> None:
    """Remove one previously registered secret."""
    if not value:
        return
    with _lock:
        _runtime_secrets.discard(value.strip())


def clear_registered_secrets() -> None:
    """Forget all registered secrets (used by tests and ``demo`` mode)."""
    with _lock:
        _runtime_secrets.clear()


def redact_text(text: str) -> str:
    """Return ``text`` with secrets and secret-shaped values replaced."""
    if not text:
        return text
    result = text
    with _lock:
        secrets = sorted(_runtime_secrets, key=len, reverse=True)
    for secret in secrets:
        if secret in result:
            result = result.replace(secret, "***")
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


_SENSITIVE_KEY_RE = re.compile(
    r"(?i)token|secret|password|passwd|api[_-]?key|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|bot[_-]?token|session|cookie|"
    r"private[_-]?key|authorization|credential"
)


def redact_mapping(data: dict[str, object]) -> dict[str, object]:
    """Redact a mapping: sensitive keys become ``***``; other strings pattern-scrubbed."""
    out: dict[str, object] = {}
    for key, value in data.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            out[key] = "***"
        elif isinstance(value, str):
            out[key] = redact_text(value)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value)
        else:
            out[key] = value
    return out


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the fully formatted message in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        return True

"""Secret redaction tests."""

from __future__ import annotations

import logging

from autoposter.utils.redact import (
    RedactingFilter,
    clear_registered_secrets,
    redact_mapping,
    redact_text,
    register_secret,
)


def setup_function() -> None:
    clear_registered_secrets()


def test_redacts_bearer_token() -> None:
    text = "Authorization: Bearer abcdefghijklmnop"
    out = redact_text(text)
    assert "abcdefghijklmnop" not in out
    assert "Bearer ***" in out


def test_redacts_key_value_pairs() -> None:
    out = redact_text('api_key=sk-1234567890abcdef and password: hunter22hunter2')
    assert "sk-1234567890abcdef" not in out
    assert "hunter22hunter2" not in out


def test_redacts_telegram_bot_token() -> None:
    out = redact_text("token is 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in out


def test_redacts_discord_webhook() -> None:
    url = "https://discord.com/api/webhooks/111111111111111111/abcdefghijklmnopqrstuvwxyz"
    out = redact_text(f"posting to {url} now")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "webhooks/***" in out


def test_redacts_fernet_key() -> None:
    key = "gAAAAABk" + "x" * 40
    out = redact_text(f"key={key}")
    assert key not in out


def test_registered_secret_replaced_anywhere() -> None:
    register_secret("SuperSecretValue123")
    out = redact_text("connecting with SuperSecretValue123 to the api")
    assert "SuperSecretValue123" not in out
    assert "***" in out


def test_short_values_not_registered() -> None:
    register_secret("abc")
    assert redact_text("abc def") == "abc def"


def test_redact_mapping_nested() -> None:
    data = {"token": "secret-value-123", "nested": {"password": "hunter22hunter2", "n": 1}}
    out = redact_mapping(data)
    assert out["token"] == "***"
    assert out["nested"]["password"] == "***"  # type: ignore[index]
    assert out["nested"]["n"] == 1  # type: ignore[index]


def test_redact_text_handles_empty() -> None:
    assert redact_text("") == ""


def test_logging_filter_redacts(caplog: object) -> None:
    logger = logging.getLogger("autoposter.test.redact")
    logger.setLevel(logging.INFO)
    filter_ = RedactingFilter()
    record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="using token %s now",
        args=("123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",),
        exc_info=None,
    )
    assert filter_.filter(record) is True
    assert "AAHdqTcv" not in record.getMessage()

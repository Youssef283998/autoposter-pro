"""Pydantic schema for ``config.yaml`` (behavior) and related sub-models."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoposter.utils.timeutil import get_zone


class RetryConfig(BaseModel):
    """Retry/backoff policy for retryable and rate-limited failures."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=5, ge=1, le=50)
    base_delay_seconds: float = Field(default=2.0, gt=0)
    max_delay_seconds: float = Field(default=300.0, gt=0)
    jitter: float = Field(default=0.25, ge=0.0, le=1.0)


class CircuitBreakerConfig(BaseModel):
    """Per-platform circuit breaker so one dead API cannot stall the rest."""

    model_config = ConfigDict(extra="forbid")

    failure_threshold: int = Field(default=5, ge=1)
    cooldown_seconds: float = Field(default=120.0, gt=0)


class DedupConfig(BaseModel):
    """Duplicate-content prevention within a time window per account."""

    model_config = ConfigDict(extra="forbid")

    window_hours: int = Field(default=24, ge=0, le=24 * 365)
    mode: Literal["warn", "block"] = "warn"


class MissedPolicy(BaseModel):
    """What to do when the machine was off at a post's scheduled time."""

    model_config = ConfigDict(extra="forbid")

    post_late_within_minutes: int = Field(default=30, ge=0)
    max_catchup_posts_per_tick: int = Field(default=1, ge=0)


class PlatformCaps(BaseModel):
    """Per-platform rate caps and randomized inter-post delay."""

    model_config = ConfigDict(extra="forbid")

    hourly: int | None = Field(default=None, ge=1)
    daily: int | None = Field(default=None, ge=1)
    delay_between_posts_seconds: tuple[float, float] = (1.0, 4.0)

    @field_validator("delay_between_posts_seconds")
    @classmethod
    def _valid_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        low, high = value
        if low < 0 or high < low:
            raise ValueError("delay_between_posts_seconds must satisfy 0 <= min <= max")
        return (low, high)


class QuietHours(BaseModel):
    """Daily local-time window during which posts are held back."""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("expected HH:MM")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("expected a valid 24-hour time")
        return value


class MediaConfig(BaseModel):
    """Media validation and processing behavior."""

    model_config = ConfigDict(extra="forbid")

    auto_resize: bool = True
    max_upload_mb: int = Field(default=25, ge=1, le=1024)
    keep_originals: bool = True
    image_max_width: int = Field(default=2048, ge=16, le=10000)
    image_quality: int = Field(default=85, ge=1, le=100)


class AlertChannel(BaseModel):
    """One notification channel (kind-specific secrets live in .env)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["telegram", "smtp", "webhook"]
    settings: dict[str, str] = Field(default_factory=dict)
    min_interval_seconds: float = Field(default=300.0, ge=0)


class WebConfig(BaseModel):
    """Local dashboard bind address. Localhost unless you deliberately change it."""

    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8300, ge=1, le=65535)


class Config(BaseModel):
    """Root of ``config.yaml`` — behavior only, never secrets."""

    model_config = ConfigDict(extra="forbid")

    default_timezone: str = "UTC"
    tick_seconds: int = Field(default=5, ge=1, le=300)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    missed: MissedPolicy = Field(default_factory=MissedPolicy)
    platforms: dict[str, PlatformCaps] = Field(default_factory=dict)
    quiet_hours: QuietHours | None = None
    media: MediaConfig = Field(default_factory=MediaConfig)
    alerts: list[AlertChannel] = Field(default_factory=list)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = False
    data_dir: Path = Path("~/.autoposter")
    web: WebConfig = Field(default_factory=WebConfig)

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        get_zone(value)  # raises ConfigError with a friendly fix
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    def platform_caps(self, platform: str) -> PlatformCaps:
        """Caps for a platform; conservative defaults when not configured."""
        if platform in self.platforms:
            return self.platforms[platform]
        return PlatformCaps(hourly=30, daily=500, delay_between_posts_seconds=(1.0, 4.0))

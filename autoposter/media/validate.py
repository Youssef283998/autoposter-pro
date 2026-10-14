"""Media validation: MIME-by-content, size, dimensions, video duration."""

from __future__ import annotations

from pathlib import Path

import filetype

from autoposter.core.models import MediaItem, PlatformLimits, ValidationIssue
from autoposter.utils.logging import get_logger

logger = get_logger("media.validate")


def detect_mime(path: Path) -> str:
    """Detect MIME type by content (not extension)."""
    kind = filetype.guess(str(path))
    if kind is None:
        return "application/octet-stream"
    return str(kind.mime)


def get_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Return (width, height) for an image, or None if unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            size: tuple[int, int] = img.size
            return size
    except Exception:
        return None


def get_video_duration(path: Path) -> float | None:
    """Return video duration in seconds via ffprobe, or None."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def validate_media(
    items: list[MediaItem],
    limits: PlatformLimits,
) -> list[ValidationIssue]:
    """Validate a list of media items against platform limits."""
    issues: list[ValidationIssue] = []
    if len(items) > limits.max_media:
        issues.append(
            ValidationIssue(
                field="media",
                message=f"{len(items)} items exceeds limit of {limits.max_media}.",
            )
        )
    for i, item in enumerate(items):
        issues.extend(validate_one(item, limits, prefix=f"media[{i}]"))
    return issues


def validate_one(
    item: MediaItem, limits: PlatformLimits, *, prefix: str = "media"
) -> list[ValidationIssue]:
    """Validate one media item."""
    issues: list[ValidationIssue] = []
    path = item.stored_path or item.source_path

    if not path.is_file():
        issues.append(
            ValidationIssue(field=prefix, message=f"File not found: {path}")
        )
        return issues

    # MIME by content.
    actual_mime = detect_mime(path)
    if actual_mime != item.mime:
        issues.append(
            ValidationIssue(
                field=prefix,
                message=f"Declared MIME {item.mime} but content is {actual_mime}.",
            )
        )
    if actual_mime not in limits.allowed_mime:
        issues.append(
            ValidationIssue(
                field=prefix,
                message=f"MIME {actual_mime} not allowed.",
            )
        )

    # Size.
    size = path.stat().st_size
    if size > limits.max_bytes:
        issues.append(
            ValidationIssue(
                field=prefix,
                message=f"File is {size} bytes; limit is {limits.max_bytes}.",
            )
        )

    # Image dimensions.
    if actual_mime.startswith("image/"):
        dims = get_image_dimensions(path)
        if dims is None:
            issues.append(
                ValidationIssue(
                    field=prefix,
                    message="Could not read image dimensions (corrupt?).",
                )
            )

    # Video duration.
    if actual_mime.startswith("video/") and limits.max_video_seconds is not None:
        duration = get_video_duration(path)
        if duration is not None and duration > limits.max_video_seconds:
            issues.append(
                ValidationIssue(
                    field=prefix,
                    message=(
                        f"Video is {duration:.1f}s; limit is "
                        f"{limits.max_video_seconds}s."
                    ),
                )
            )

    return issues

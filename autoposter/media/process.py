"""Media processing: image resize and compression via Pillow."""

from __future__ import annotations

from pathlib import Path

from autoposter.core.errors import ConfigError
from autoposter.utils.logging import get_logger

logger = get_logger("media.process")


def resize_image(
    source: Path,
    destination: Path,
    *,
    max_width: int = 2048,
    quality: int = 85,
) -> Path:
    """Downscale an image so its longest edge is at most ``max_width``.

    Preserves aspect ratio. Converts to RGB before saving as JPEG/WebP.
    """
    try:
        from PIL import Image

        with Image.open(source) as img:
            img = img.convert("RGB")
            width, height = img.size
            if width > max_width or height > max_width:
                ratio = max_width / max(width, height)
                new_size = (int(width * ratio), int(height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            destination.parent.mkdir(parents=True, exist_ok=True)
            img.save(destination, quality=quality, optimize=True)
    except Exception as exc:
        raise ConfigError(f"Failed to resize image {source}: {exc}") from exc
    return destination


def compress_image(
    source: Path,
    destination: Path,
    *,
    quality: int = 85,
) -> Path:
    """Recompress an image in place (or to destination) at the given quality."""
    from PIL import Image

    with Image.open(source) as img:
        img = img.convert("RGB")
        destination.parent.mkdir(parents=True, exist_ok=True)
        img.save(destination, quality=quality, optimize=True)
    return destination


def needs_resize(path: Path, max_width: int) -> bool:
    """Whether an image is wider/taller than ``max_width``."""
    from PIL import Image

    try:
        with Image.open(path) as img:
            width, height = img.size
            return bool(width > max_width or height > max_width)
    except Exception:
        return False

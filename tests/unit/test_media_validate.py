"""Media validation, processing and store tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from autoposter.core.models import MediaItem, PlatformLimits
from autoposter.media.process import needs_resize, resize_image
from autoposter.media.store import MediaStore
from autoposter.media.validate import detect_mime, get_image_dimensions, validate_one


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    path = tmp_path / "test.png"
    img = Image.new("RGB", (100, 80), color="red")
    img.save(path)
    return path


@pytest.fixture
def sample_jpg(tmp_path: Path) -> Path:
    path = tmp_path / "test.jpg"
    img = Image.new("RGB", (50, 50), color="blue")
    img.save(path, "JPEG")
    return path


def test_detect_mime_png(sample_image: Path) -> None:
    assert detect_mime(sample_image) == "image/png"


def test_detect_mime_jpg(sample_jpg: Path) -> None:
    assert detect_mime(sample_jpg) == "image/jpeg"


def test_get_image_dimensions(sample_image: Path) -> None:
    assert get_image_dimensions(sample_image) == (100, 80)


def test_validate_one_ok(sample_image: Path) -> None:
    item = MediaItem(
        source_path=sample_image,
        stored_path=sample_image,
        mime="image/png",
        size=sample_image.stat().st_size,
        sha256="a" * 64,
    )
    limits = PlatformLimits(
        max_chars=100,
        max_media=4,
        allowed_mime=frozenset({"image/png"}),
        max_bytes=1024 * 1024,
    )
    assert validate_one(item, limits) == []


def test_validate_one_missing_file(tmp_path: Path) -> None:
    item = MediaItem(
        source_path=tmp_path / "nope.png",
        mime="image/png",
        size=10,
        sha256="a" * 64,
    )
    limits = PlatformLimits(
        max_chars=100,
        max_media=4,
        allowed_mime=frozenset({"image/png"}),
        max_bytes=1024 * 1024,
    )
    issues = validate_one(item, limits)
    assert any("not found" in i.message.lower() for i in issues)


def test_validate_one_wrong_mime(sample_image: Path) -> None:
    item = MediaItem(
        source_path=sample_image,
        stored_path=sample_image,
        mime="image/jpeg",  # actually PNG
        size=sample_image.stat().st_size,
        sha256="a" * 64,
    )
    limits = PlatformLimits(
        max_chars=100,
        max_media=4,
        allowed_mime=frozenset({"image/png"}),
        max_bytes=1024 * 1024,
    )
    issues = validate_one(item, limits)
    assert any("content is" in i.message for i in issues)


def test_validate_one_too_large(sample_image: Path) -> None:
    item = MediaItem(
        source_path=sample_image,
        stored_path=sample_image,
        mime="image/png",
        size=sample_image.stat().st_size,
        sha256="a" * 64,
    )
    limits = PlatformLimits(
        max_chars=100,
        max_media=4,
        allowed_mime=frozenset({"image/png"}),
        max_bytes=10,  # tiny limit
    )
    issues = validate_one(item, limits)
    assert any("bytes" in i.message for i in issues)


def test_resize_image_downscales(sample_image: Path, tmp_path: Path) -> None:
    dest = tmp_path / "resized.png"
    resize_image(sample_image, dest, max_width=50)
    dims = get_image_dimensions(dest)
    assert dims is not None
    assert dims[0] <= 50
    assert dims[1] <= 50


def test_resize_image_small_unchanged(sample_jpg: Path, tmp_path: Path) -> None:
    dest = tmp_path / "resized.jpg"
    resize_image(sample_jpg, dest, max_width=2048)
    dims = get_image_dimensions(dest)
    assert dims == (50, 50)


def test_needs_resize(sample_image: Path) -> None:
    assert needs_resize(sample_image, 50)
    assert not needs_resize(sample_image, 2048)


def test_media_store_roundtrip(sample_image: Path, tmp_path: Path) -> None:
    store = MediaStore(tmp_path / "store")
    item = store.store(sample_image, mime="image/png")
    assert item.stored_path is not None
    assert item.stored_path.exists()
    assert item.sha256 == "a" * 64 or len(item.sha256) == 64
    assert item.size == sample_image.stat().st_size


def test_media_store_dedup(sample_image: Path, tmp_path: Path) -> None:
    store = MediaStore(tmp_path / "store")
    item1 = store.store(sample_image, mime="image/png")
    item2 = store.store(sample_image, mime="image/png")
    assert item1.stored_path == item2.stored_path


def test_media_store_path_for(tmp_path: Path) -> None:
    store = MediaStore(tmp_path / "store")
    path = store.path_for("a" * 64, ".png")
    assert "aa" in str(path)
    assert "aa" in str(path)
    assert path.name == "a" * 64 + ".png"

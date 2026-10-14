"""Internal content-addressed media store.

Files are stored by SHA-256 hash so deleting the original never breaks a
scheduled post, and identical files are stored only once.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from autoposter.core.models import MediaItem
from autoposter.utils.logging import get_logger

logger = get_logger("media.store")


class MediaStore:
    """Hash-named media storage under a root directory."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def store(
        self,
        source: Path,
        *,
        alt_text: str | None = None,
        mime: str | None = None,
        keep_original: bool = True,
    ) -> MediaItem:
        """Copy ``source`` into the store and return a MediaItem.

        The file is named ``<sha256>.<ext>`` inside a two-level shard
        directory (``ab/cd/<hash>.<ext>``) to avoid huge flat directories.
        """
        sha256 = _hash_file(source)
        ext = source.suffix.lower() or ".bin"
        shard = self._root / sha256[:2] / sha256[2:4]
        destination = shard / f"{sha256}{ext}"
        destination.parent.mkdir(parents=True, exist_ok=True)

        if not destination.exists():
            if keep_original:
                shutil.copy2(source, destination)
            else:
                shutil.move(str(source), str(destination))

        return MediaItem(
            source_path=source,
            stored_path=destination,
            mime=mime or "application/octet-stream",
            size=destination.stat().st_size,
            sha256=sha256,
            alt_text=alt_text,
        )

    def path_for(self, sha256: str, ext: str = ".bin") -> Path:
        """Return the stored path for a hash (may not exist)."""
        if not re.match(r"^[a-f0-9]{64}$", sha256):
            raise ValueError(f"Invalid SHA-256 hash: {sha256!r}")
        return self._root / sha256[:2] / sha256[2:4] / f"{sha256}{ext}"

    def exists(self, sha256: str, ext: str = ".bin") -> bool:
        """Whether a file with this hash is already stored."""
        return self.path_for(sha256, ext).exists()


def _hash_file(path: Path) -> str:
    """SHA-256 of a file's contents."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

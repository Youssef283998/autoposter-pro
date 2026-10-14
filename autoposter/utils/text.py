"""Text helpers: grapheme-aware length, X link counting, hash normalization."""

from __future__ import annotations

import re
import unicodedata

import grapheme

# Matches http(s)://... up to whitespace or common terminators.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# Collapse all whitespace runs (including newlines) to a single space for hashing.
_WS_RE = re.compile(r"\s+")


def grapheme_len(text: str) -> int:
    """Number of user-perceived characters (emoji-safe)."""
    return int(grapheme.length(text))


def normalize_for_hash(text: str) -> str:
    """NFC-normalize and collapse whitespace so cosmetic edits hash equally."""
    normalized = unicodedata.normalize("NFC", text)
    return _WS_RE.sub(" ", normalized).strip()


def count_x_length(text: str, *, link_weight: int = 23) -> int:
    """Approximate X/Twitter weighted length: every URL counts as ``link_weight``.

    This matches X's current behavior where t.co-wrapped links are counted as a
    fixed 23 characters regardless of their real length. The approximation
    treats every ``http(s)://`` run as one link; X's official counting rules
    edge-case some punctuation boundaries — adapters may tighten further.
    """
    total = 0
    last = 0
    for match in _URL_RE.finditer(text):
        total += grapheme_len(text[last : match.start()])
        total += link_weight
        last = match.end()
    total += grapheme_len(text[last:])
    return total


def split_hashtags(text: str) -> list[str]:
    """Extract hashtags (without the ``#``) from text."""
    return re.findall(r"(?<!\w)#([\w_]+)", text, flags=re.UNICODE)


def sanitize_filename(name: str, *, max_len: int = 120) -> str:
    """Reduce an arbitrary filename to a safe basename (no path separators)."""
    base = name.replace("\x00", "")
    base = re.sub(r"[\\/\0]", "_", base)
    base = base.strip().rstrip(".")
    if not base:
        base = "file"
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 12:
            keep = max_len - len(ext) - 1
            base = f"{stem[:keep]}.{ext}"
        else:
            base = base[:max_len]
    return base

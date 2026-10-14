"""Text helper tests: grapheme length, X link counting, normalization."""

from __future__ import annotations

from autoposter.utils.text import (
    count_x_length,
    grapheme_len,
    normalize_for_hash,
    sanitize_filename,
    split_hashtags,
)


def test_grapheme_len_ascii() -> None:
    assert grapheme_len("hello") == 5


def test_grapheme_len_emoji_sequences() -> None:
    family = "\U0001F468‍\U0001F469‍\U0001F467"  # 👨‍👩‍👧
    assert grapheme_len(family) == 1
    flag = "\U0001F1EF\U0001F1F5"  # 🇯🇵
    assert grapheme_len(flag) == 1
    assert grapheme_len("👍🏽") == 1


def test_normalize_collapses_whitespace() -> None:
    assert normalize_for_hash("a\n\n  b\t c ") == "a b c"


def test_normalize_nfc() -> None:
    assert normalize_for_hash("café") == normalize_for_hash("café")


def test_count_x_length_plain() -> None:
    assert count_x_length("hello") == 5


def test_count_x_length_url_fixed_weight() -> None:
    text = "check https://example.com/very/long/path/that/goes/on/forever"
    assert count_x_length(text) == len("check ") + 23


def test_count_x_length_multiple_urls() -> None:
    text = "a https://one.example b https://two.example c"
    # "a " + 23 + " b " + 23 + " c" = 2+23+3+23+2 = 53
    assert count_x_length(text) == 53


def test_split_hashtags() -> None:
    assert split_hashtags("Launching #ProductLaunch and #saas_today!") == [
        "ProductLaunch",
        "saas_today",
    ]


def test_sanitize_filename_strips_paths() -> None:
    assert sanitize_filename("../../etc/passwd") == ".._.._etc_passwd"
    assert sanitize_filename("") == "file"


def test_sanitize_filename_keeps_extension() -> None:
    long_name = "x" * 200 + ".jpg"
    result = sanitize_filename(long_name)
    assert result.endswith(".jpg")
    assert len(result) <= 120

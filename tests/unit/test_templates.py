"""Template and spintax tests."""

from __future__ import annotations

import random

from autoposter.core.templates import expand_body, missing_variables, render_template, spintax


def test_render_template_substitutes() -> None:
    out = render_template(
        "Launch of {product} at {discount}% off!", {"product": "Pro", "discount": "20"}
    )
    assert out == "Launch of Pro at 20% off!"


def test_render_template_leaves_unknown() -> None:
    out = render_template("Hi {name}, meet {team}", {"name": "Ada"})
    assert out == "Hi Ada, meet {team}"


def test_missing_variables() -> None:
    assert missing_variables("{a} {b} {a}", {"a": "1"}) == ["b"]


def test_spintax_picks_one_option() -> None:
    rng = random.Random(0)
    out = spintax("{Hello|Hi} there", rng=rng)
    assert out in ("Hello there", "Hi there")


def test_spintax_nested() -> None:
    rng = random.Random(1)
    out = spintax("{Good morning|{Hey|Yo}} friend", rng=rng)
    assert out in ("Good morning friend", "Hey friend", "Yo friend")


def test_spintax_leaves_plain_braces() -> None:
    assert spintax("no spin {here}") == "no spin {here}"


def test_expand_body_template_then_spintax() -> None:
    rng = random.Random(2)
    out = expand_body(
        "{greeting} world",
        variables={"greeting": "{Hello|Hi}"},
        use_spintax=True,
        rng=rng,
    )
    assert out in ("Hello world", "Hi world")


def test_expand_body_without_spintax_keeps_braces() -> None:
    out = expand_body("{a|b}", variables={}, use_spintax=False)
    assert out == "{a|b}"

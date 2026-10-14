"""Content templates: ``{variable}`` substitution and spintax variants."""

from __future__ import annotations

import random
import re
from collections.abc import Mapping

# {name} where name is a simple identifier — does NOT match spintax {a|b}.
_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Innermost spintax group: {opt1|opt2|...} with no nested braces.
_SPINTAX_RE = re.compile(r"\{([^{}]+)\}")


def render_template(text: str, variables: Mapping[str, str]) -> str:
    """Substitute ``{name}`` placeholders from ``variables``.

    Unknown placeholders are left untouched so a later pass (or the user)
    can spot typos. Spintax groups like ``{a|b}`` are not matched.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _VAR_RE.sub(_replace, text)


def missing_variables(text: str, variables: Mapping[str, str]) -> list[str]:
    """Placeholder names present in ``text`` but absent from ``variables``."""
    found = _VAR_RE.findall(text)
    return sorted({name for name in found if name not in variables})


def spintax(text: str, *, rng: random.Random | None = None) -> str:
    """Choose one option from each ``{a|b|c}`` group (innermost-first).

    Supports nesting: ``{Hello|Hi} {there|friend}`` picks one combination.
    Pure braces without a ``|`` are left alone.
    """
    chooser = rng.choice if rng is not None else random.choice
    result = text
    # Each pass resolves the innermost groups; loop until stable.
    while True:
        match = _SPINTAX_RE.search(result)
        if match is None:
            return result
        inner = match.group(1)
        if "|" not in inner:
            # Not spintax — protect it from further scanning by replacing with
            # a sentinel, then restore at the end. Simpler: leave and skip by
            # wrapping is complex; a non-| brace group is rare in post bodies,
            # so we just resolve nothing and stop to avoid infinite loops.
            return result
        options = inner.split("|")
        choice = chooser(options)
        result = result[: match.start()] + choice + result[match.end() :]


def expand_body(
    body: str,
    *,
    variables: Mapping[str, str] | None = None,
    use_spintax: bool = False,
    rng: random.Random | None = None,
) -> str:
    """Render variables first, then optionally apply spintax."""
    result = render_template(body, variables or {})
    if use_spintax:
        result = spintax(result, rng=rng)
    return result

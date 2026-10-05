"""Regex pattern interpolation (cf. spec §8.2)."""

import re

from catalog_matching.models import TargetSegment

# Detects ONLY identifier placeholders ``{name}``; a regex quantifier like
# ``{2,4}`` or ``{3}`` is not an identifier and is left untouched.
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class InterpolationError(Exception):
    """Interpolation error: unknown placeholder."""


def interpolate(pattern: str, target: TargetSegment) -> str:
    """Substitutes the whitelist ``{season} {seasonal_number} {absolute_number} {segment}
    {title}``.

    All values are inserted ``re.escape``-d (literal). Any other placeholder raises
    :class:`InterpolationError`.
    """

    def replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name == "season":
            return str(re.escape(str(target.season)))
        if name == "seasonal_number":
            return str(re.escape(str(target.seasonal_number)))
        if name == "absolute_number":
            return str(re.escape(str(target.absolute_number)))
        if name == "segment":
            return str(re.escape(target.segment.upper()))
        if name == "title":
            return str(re.escape(target.title))
        raise InterpolationError(f"unknown placeholder: {{{name}}}")

    return _PLACEHOLDER.sub(replace, pattern)

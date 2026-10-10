"""Guard "logic-free Jinja templates" — token-match approach.

Rule: a template may contain only passive constructs.

Allowed
-------
- Structural tags     : {% extends %} {% block %} {% endblock %} {% include %}
                        {% for %} {% else %} {% endfor %}
- Simple interpolation: {{ x }}  {{ x.attr }}  {{ x.attr.sub }}
  (identifier + attribute access via `.`, no operator or call)

Rejected
--------
- Logic tags          : {% if … %}  {% elif … %}  {% set … %}  {% macro … %}
- Computed expressions: any {{ … }} expression containing:
    · an arithmetic or comparison operator: + - * / % == != < >
    · a filter                            : |
    · a function call                     : (

Regexes used
------------
1. Forbidden tag    : ``r"\\{%-?\\s*(if|elif|set|macro)\\b"``
   (detects ``{% if``, ``{%- if``, ``{% set``, etc.)

2. Illegal expression: in ``{{ … }}``, presence of one of the chars ``+ - * / % = ! < > | (``
   (we don't distinguish `!=` from a lone `!` — sufficient, no legitimate `!` in Jinja).
   Regex: ``r"\\{\\{[^}]*[+\\-*/%=!<>|(][^}]*\\}\\}"``
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# --- Forbidden patterns ---

# {% if %} / {% elif %} / {% set %} / {% macro %} tags
_FORBIDDEN_TAG: re.Pattern[str] = re.compile(r"\{%-?\s*(if|elif|set|macro)\b")

# {{ … }} expression containing an operator or a call
_FORBIDDEN_EXPR: re.Pattern[str] = re.compile(r"\{\{[^}]*[+\-*/%=!<>|(][^}]*\}\}")


def _check_file(path: Path) -> list[str]:
    """Return the violations found in *path* (empty list = compliant)."""
    text = path.read_text(encoding="utf-8")
    violations: list[str] = []

    for m in _FORBIDDEN_TAG.finditer(text):
        keyword = m.group(1)
        violations.append(f"{path.name}: forbidden tag '{{% {keyword} %}}'")

    for m in _FORBIDDEN_EXPR.finditer(text):
        snippet = m.group(0)[:60]
        violations.append(f"{path.name}: computed expression '{snippet}'")

    return violations


def find_logic_violations(directory: Path) -> list[str]:
    """Scan *directory* recursively, return all violations found.

    Each entry has the form ``"<file>: <reason>"``.
    """
    violations: list[str] = []
    for html_file in sorted(directory.rglob("*.html")):
        violations.extend(_check_file(html_file))
    return violations


def main() -> None:
    """CLI entry point: ``python -m p2pwatch.webui._dev.check_templates <directory>``."""
    if len(sys.argv) < 2:
        sys.exit("usage: check_templates <dir>")
    directory = Path(sys.argv[1])
    violations = find_logic_violations(directory)
    if violations:
        for v in violations:
            print(v)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()  # pragma: no cover

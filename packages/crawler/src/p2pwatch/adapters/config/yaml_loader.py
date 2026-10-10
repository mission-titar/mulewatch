"""Adapter: reads a YAML file into Python structures (cf. spec §4 I/O boundary).

The ONLY module in the project that imports ``yaml`` and touches the filesystem for
config. Does NOT validate the substance (schema/graph/RE2): that's the domain's job
(``catalog_matching.validation``). Minimal guardrail: the root must be a mapping.
"""

from pathlib import Path
from typing import Any

import yaml


class YamlLoadError(Exception):
    """The YAML file is unreadable or its root is not a mapping."""


def load_yaml(path: Path) -> dict[str, Any]:
    """Reads ``path`` and returns its root (a mapping) parsed by ``yaml.safe_load``.

    ``safe_load`` parses ISO dates into ``datetime.date`` and instantiates no arbitrary
    Python object (no unsafe ``yaml.load``). The adapter's error boundary:
    unreadable file, invalid YAML, or non-mapping root (list, scalar, empty file
    → ``None``) all raise :class:`YamlLoadError`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlLoadError(f"unreadable YAML file: {path} ({exc})") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise YamlLoadError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise YamlLoadError(f"YAML root expected = mapping, got {type(raw).__name__} ({path})")
    return raw

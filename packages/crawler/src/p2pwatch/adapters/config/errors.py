"""Shared config error (fail-fast §5/§14), isolated to break an import cycle."""


class ConfigError(Exception):
    """Invalid config → refuse to start (fail-fast, spec §5/§14)."""

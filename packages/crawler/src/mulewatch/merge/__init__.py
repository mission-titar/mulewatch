"""Standalone catalog merge script (N ``catalog.db`` → 1, idempotent).

**Tool** subpackage of the crawler package, fully disjoint from the app (spec
``agents/specs/2026-06-15-fusion-merge-design.md``): it imports only ``open_catalog`` and its
errors (to create or migrate the output) + the stdlib. Entry point: ``python -m mulewatch.merge``.
"""

"""Entry point ``python -m mulewatch.merge``: safe-by-default merge CLI.

Aligned with ``tools/ec_probe.py``/``composition/__main__.py`` (no ``[project.scripts]``:
invoked as ``python -m mulewatch.merge``). ``main(argv) -> int`` returns an exit code:
``0`` = merge OK; ``2`` = usage/merge error (clear message on ``stderr``, never a bare
traceback); argparse itself returns ``2`` for a parsing error (mutually
exclusive/required group). No environment variable (repo doctrine, spec §6).

Safe-by-default (zero accidental loss, spec §6):
- ``--output`` new → created + merged; ``--output`` existing → refused UNLESS ``--force`` (append
  idempotent, never a truncate).
- ``--into <source>`` → dest = one of the sources, which MUST be listed; the others are merged
  into it. Mutually exclusive with ``--output``; exactly one of the two is required.
- ``--force`` only makes sense with ``--output`` (rejected with ``--into``).
- a missing source → ``MergeError`` BEFORE creating/opening the output (fail-fast).
"""

import argparse
import logging
import sys
from pathlib import Path

from mulewatch.merge.errors import MergeError
from mulewatch.merge.merger import merge_catalogs

_LOGGER = logging.getLogger("mulewatch.merge")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mulewatch.merge",
        description="Merge N catalog.db into one (idempotent, append-only preserved).",
    )
    parser.add_argument(
        "sources", nargs="+", type=Path, help="1..N catalog.db files to merge (≥ 1)."
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--output",
        "-o",
        type=Path,
        help="FRESH output file (refused if it exists, unless --force).",
    )
    destination.add_argument(
        "--into", type=Path, help="Destination = one of the listed sources (merged into it)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow an existing --output (idempotent append, never truncate).",
    )
    return parser.parse_args(argv)


def _resolve_destination(args: argparse.Namespace) -> tuple[Path, bool]:
    """Validates the safe-by-default rules and returns ``(output, dest_is_source)``.

    Raises ``MergeError`` (clear message) BEFORE any opening/creation of the output:
    missing sources, ``--force`` with ``--into``, existing ``--output`` without ``--force``,
    ``--into`` not listed in the sources.
    """
    for source in args.sources:
        if not source.exists():
            raise MergeError(f"source not found: {source}")

    if args.into is not None:
        if args.force:
            raise MergeError("--force makes no sense with --into")
        resolved_sources = {source.resolve() for source in args.sources}
        if args.into.resolve() not in resolved_sources:
            raise MergeError(f"--into must name a listed source: {args.into}")
        return args.into, True

    # --output mode (the required group guarantees we have one OR the other; here it's --output).
    if args.output.exists() and not args.force:
        raise MergeError(f"output already exists: {args.output} (use --force to merge into it)")
    return args.output, False


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ``0`` = OK, ``2`` = usage/merge error (clear message on stderr)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        output, dest_is_source = _resolve_destination(args)
        _LOGGER.info("merge → %s (%d source(s))", output, len(args.sources))
        merge_catalogs(output, args.sources, dest_is_source=dest_is_source)
    except MergeError as error:
        print(f"Merge failed: {error}", file=sys.stderr, flush=True)
        return 2
    _LOGGER.info("merge done: %s", output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

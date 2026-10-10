"""Entry point ``python -m p2pwatch``: loads the config, builds the app, runs (§4).

OBSERVER mode (spec §2): observe, catalog, decide, loop — nothing else. DOWNLOAD mode
activates via ``download.enabled: true`` in the config (the unified parser then wires the
download loop). Loads the unified crawler config (``crawler.yml``) + ``targets`` +
the matcher config (fail-fast at the slightest issue → refuses to start, spec §5/§14), assembles the
real adapters (clock/RNG/nudge), then ``asyncio.run(app.run())``. The clean & bounded shutdown is
carried by ``CrawlerApp`` (spec §6).

The config paths are passed as arguments (``--config``/``--targets``/``--matcher``) with
defaults ``deploy/*.yml``. The sensitive config values (secrets, URLs) are
interpolated from the environment via ``${NAME}`` (``os.environ``, config adapter).
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from catalog_matching.validation import (
    ConfigError as MatcherConfigError,
)
from catalog_matching.validation import (
    parse_matcher_config,
    parse_targets,
)
from p2pwatch.adapters.clock_asyncio import AsyncioClock, SeededRng
from p2pwatch.adapters.config.crawler_config import ConfigError, parse_crawler_config
from p2pwatch.adapters.config.yaml_loader import YamlLoadError, load_yaml
from p2pwatch.adapters.decision_signal_asyncio import AsyncioDecisionSignal
from p2pwatch.composition.app import CrawlerApp
from p2pwatch.domain.policy_fingerprint import policy_fingerprint


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    """The config paths (same options, same defaults) for both run AND validate-config."""
    parser.add_argument("--config", type=Path, default=Path("deploy/crawler.yml"))
    parser.add_argument("--targets", type=Path, default=Path("deploy/targets.yml"))
    parser.add_argument("--matcher", type=Path, default=Path("deploy/matcher.yml"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="p2pwatch", description="eMule crawler (observer)")
    _add_config_options(parser)
    return parser.parse_args(argv)


def _parse_validate_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p2pwatch validate-config",
        description="Load + validate the config (matcher/targets/crawler), start nothing",
    )
    _add_config_options(parser)
    return parser.parse_args(argv)


def build_app(args: argparse.Namespace) -> CrawlerApp:
    """Loads + validates all config (fail-fast §5/§14) and assembles the ``CrawlerApp``.

    The unified crawler config is interpolated from ``os.environ`` (``${NAME}`` → secrets/URLs).
    Any error (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) propagates as-is:
    ``main`` catches it, logs clearly, and refuses to start (spec §14).

    The policy fingerprint (spec §7.1) is computed here, once, from the RAW bytes of
    ``matcher.yml``/``targets.yml`` (both feed ``MatchingEngine``) — a source-level fingerprint,
    not derived from the parsed config, so a comment/whitespace-only edit still triggers one
    harmless backfill pass at the next start.
    """
    crawler_config = parse_crawler_config(load_yaml(args.config), os.environ)
    if crawler_config.observability is not None:
        logging.getLogger().setLevel(crawler_config.observability.log_level)
    targets = parse_targets(load_yaml(args.targets))
    matcher_config = parse_matcher_config(load_yaml(args.matcher))
    fingerprint = policy_fingerprint(args.matcher.read_bytes(), args.targets.read_bytes())
    return CrawlerApp(
        crawler_config=crawler_config,
        targets=targets,
        matcher_config=matcher_config,
        clock=AsyncioClock(),
        rng=SeededRng(),
        signal_hub=AsyncioDecisionSignal(),
        policy_fingerprint=fingerprint,
    )


def validate_config(argv: list[str]) -> int:
    """Loads + validates the config via the EXISTING parsers — a PURE check, starts NOTHING.

    Strictly reuses ``load_yaml`` + ``parse_{crawler_config,targets,matcher_config}`` (no new
    validation logic). Since the config is interpolated from ``os.environ``, the INTENDED side
    effect is that we ALSO validate the presence of the env variables referenced by the
    ACTIVE sections. Any error (``YamlLoadError``/``ConfigError``/``MatcherConfigError``) →
    clear message on stderr + code 1, like the run's refusal to start.
    """
    args = _parse_validate_args(argv)
    try:
        parse_crawler_config(load_yaml(args.config), os.environ)
        parse_targets(load_yaml(args.targets))
        parse_matcher_config(load_yaml(args.matcher))
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Invalid config: {error}", file=sys.stderr, flush=True)
        return 1
    print("Config valid", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns an exit code (0 = clean shutdown, 1 = invalid config).

    The ``try`` ALSO covers ``asyncio.run(app.run())``: a ``ConfigError`` raised at RUNTIME by
    ``run`` is a refusal to start just like a build-time invalid config, so we render it with the
    SAME clean message + non-zero exit code (instead of a bare traceback). The resources are
    already closed cleanly by ``run`` (LIFO stack) before the raise.

    Docker restarts the container after any exit, so the code only reports. ``TimeoutError``
    from ``run``'s shutdown deadline maps to 0: the bound firing is the app KEEPING its promise
    not to hang. It is a bare ``TimeoutError`` only when the deadline itself fired; a worker
    timing out surfaces as an ``ExceptionGroup`` and still exits non-zero, as a crash should.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tokens = sys.argv[1:] if argv is None else argv
    # Dispatch up front: the ``validate-config`` subcommand routes to the pure check. The BARE
    # invocation (no subcommand) falls back EXACTLY onto the run path — a backward-compat
    # constraint (compose runs ``python -m p2pwatch --config … --targets …`` with no subcmd).
    if tokens and tokens[0] == "validate-config":
        return validate_config(list(tokens[1:]))
    args = _parse_args(tokens)
    try:
        app = build_app(args)
        asyncio.run(app.run())
    except (YamlLoadError, ConfigError, MatcherConfigError) as error:
        print(f"Invalid config, refusing to start: {error}", file=sys.stderr, flush=True)
        return 1
    except TimeoutError:
        print(
            "Shutdown deadline elapsed: exiting on the bound rather than hanging.",
            file=sys.stderr,
            flush=True,
        )
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

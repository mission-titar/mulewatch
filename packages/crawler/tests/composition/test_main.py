import argparse
import logging
from pathlib import Path

import pytest

from p2pwatch.composition import __main__ as entry
from p2pwatch.composition.app import CrawlerApp
from p2pwatch.domain.policy_fingerprint import policy_fingerprint

_CONFIG = Path(__file__).resolve().parents[4] / "deploy"

# Minimal UNIFIED crawler config (policy + observer wiring), secret via ${...}. The tests that
# actually load a config write their own fixture into tmp_path.
_UNIFIED_CONFIG = """\
decision_poll_interval_seconds: 5.0
shutdown_deadline_seconds: 10.0
backoff:
  base_seconds: 2.0
  cap_seconds: 300.0
  factor: 2.0
  jitter_ratio: 0.3
amule_api_password: ${AMULE_API_PASSWORD}
catalog_db_path: /data/catalog.db
local_db_path: /data/local.db
"""

_UNIFIED_CONFIG_WITH_OBS = (
    _UNIFIED_CONFIG
    + """\
observability:
  log_level: DEBUG
  notification_timeout_seconds: 5.0
"""
)


def _write_config(tmp_path: Path, *, body: str = _UNIFIED_CONFIG) -> Path:
    path = tmp_path / "crawler.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _args(config: Path) -> argparse.Namespace:
    return argparse.Namespace(
        config=config,
        targets=_CONFIG / "targets.yml",
        matcher=_CONFIG / "matcher.yml",
    )


def _argv(config: Path) -> list[str]:
    return [
        "--config",
        str(config),
        "--targets",
        str(_CONFIG / "targets.yml"),
        "--matcher",
        str(_CONFIG / "matcher.yml"),
    ]


def test_build_app_assembles_a_crawler_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Config WITHOUT observability section → covers build_app's `observability is None` branch.
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    app = entry.build_app(_args(_write_config(tmp_path)))
    assert isinstance(app, CrawlerApp)


def test_build_app_computes_policy_fingerprint_from_matcher_and_targets_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fingerprint (Task 6) is derived from the RAW bytes of matcher.yml/targets.yml
    # (not the parsed config) and threaded into the CrawlerApp, so the startup gate can
    # compare it against the marker stored in local.db.
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    args = _args(_write_config(tmp_path))
    app = entry.build_app(args)
    expected = policy_fingerprint(args.matcher.read_bytes(), args.targets.read_bytes())
    assert app._policy_fingerprint == expected


def test_build_app_applies_log_level_when_observability_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_app's observability is not None branch calls setLevel."""
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    config = _write_config(tmp_path, body=_UNIFIED_CONFIG_WITH_OBS)
    app = entry.build_app(_args(config))
    assert isinstance(app, CrawlerApp)
    assert logging.getLogger().level == logging.DEBUG  # root level applied (DEBUG=10)


class _SpyApp:
    """Fake app: its ``run`` coroutine is never actually executed (fake asyncio.run)."""

    async def run(self) -> None:  # pragma: no cover - never awaited (asyncio.run is fake)
        return None


def test_main_returns_zero_on_clean_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # close the coroutine without running it

    monkeypatch.setattr("p2pwatch.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    assert entry.main([]) == 0


def test_main_renders_runtime_config_error_from_run_as_clean_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The full-mode gate raises a ``ConfigError`` AT RUNTIME (verifier health-check KO) from
    # ``app.run()`` — not from ``build_app``. ``main`` must render it with the SAME clean message
    # + exit code 1 (and not a bare traceback).
    from p2pwatch.adapters.config.crawler_config import ConfigError

    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # close the coroutine without running it
        raise ConfigError("verifier unreachable at startup (health-check failed)")

    monkeypatch.setattr("p2pwatch.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    code = entry.main([])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_main_returns_zero_when_the_shutdown_deadline_fires(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The bound FIRING is the app keeping its promise, not a crash (documented in __main__.main).
    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # close the coroutine without running it
        raise TimeoutError

    monkeypatch.setattr("p2pwatch.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", lambda args: _SpyApp())
    assert entry.main([]) == 0
    assert "Shutdown deadline" in capsys.readouterr().err


def test_main_refuses_to_start_on_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "crawler.yml"
    bad.write_text("catalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(["--config", str(bad)])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_main_refuses_on_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = entry.main(["--config", str(tmp_path / "absent.yml")])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_default_args_point_at_config_dir() -> None:
    namespace = entry._parse_args([])
    assert namespace.config == Path("deploy/crawler.yml")
    assert namespace.targets == Path("deploy/targets.yml")
    assert namespace.matcher == Path("deploy/matcher.yml")


def test_package_main_shim_reexports_main() -> None:
    # `python -m p2pwatch` runs the PACKAGE's __main__: it must expose the SAME
    # `main` function as composition.__main__ (otherwise DoD §9.4 is not met).
    from p2pwatch import __main__ as package_entry

    assert package_entry.main is entry.main


# ---------------------------------------------------------------- validate-config subcommand


def test_bare_invocation_still_runs_the_crawler(monkeypatch: pytest.MonkeyPatch) -> None:
    # HARD backward-compat CONSTRAINT: WITHOUT a subcommand, we fall back EXACTLY onto the
    # run path (build_app → asyncio.run). This is what compose does.
    seen: dict[str, object] = {}

    def fake_run(coro: object) -> None:
        coro.close()  # type: ignore[attr-defined]  # close the coroutine without running it

    def fake_build_app(args: argparse.Namespace) -> _SpyApp:
        seen["config"] = args.config  # proves we went through _parse_args
        return _SpyApp()

    monkeypatch.setattr("p2pwatch.composition.__main__.asyncio.run", fake_run)
    monkeypatch.setattr(entry, "build_app", fake_build_app)
    assert entry.main(_argv(Path("crawler.yml"))) == 0
    assert seen["config"] == Path("crawler.yml")


def test_validate_config_does_not_start_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # validate-config starts NOTHING: neither build_app nor asyncio.run.
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")

    def boom_run(coro: object) -> None:  # pragma: no cover - must never be called
        raise AssertionError("asyncio.run must not be called by validate-config")

    def boom_build_app(args: argparse.Namespace) -> CrawlerApp:  # pragma: no cover
        raise AssertionError("build_app must not be called by validate-config")

    monkeypatch.setattr("p2pwatch.composition.__main__.asyncio.run", boom_run)
    monkeypatch.setattr(entry, "build_app", boom_build_app)
    assert entry.main(["validate-config", *_argv(_write_config(tmp_path))]) == 0


def test_validate_config_reports_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    code = entry.main(["validate-config", *_argv(_write_config(tmp_path))])
    assert code == 0
    assert "Config valid" in capsys.readouterr().out


def test_validate_config_defaults_point_at_config_dir() -> None:
    # validate-config's options have the SAME deploy/*.yml defaults as the run.
    namespace = entry._parse_validate_args([])
    assert namespace.config == Path("deploy/crawler.yml")
    assert namespace.targets == Path("deploy/targets.yml")
    assert namespace.matcher == Path("deploy/matcher.yml")


def test_validate_config_reports_missing_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # INTENDED SIDE EFFECT: the config references ${AMULE_API_PASSWORD} (top-level key);
    # the variable missing from the environment → fail-fast interpolation → code 1, clear message.
    monkeypatch.delenv("AMULE_API_PASSWORD", raising=False)
    code = entry.main(["validate-config", *_argv(_write_config(tmp_path))])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_validate_config_rejects_broken_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "crawler.yml"
    broken.write_text("ports: [unclosed\n", encoding="utf-8")  # syntactically broken YAML
    code = entry.main(["validate-config", "--config", str(broken)])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_validate_config_rejects_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "crawler.yml"
    bad.write_text("catalog_db_path: c\nlocal_db_path: l\n", encoding="utf-8")
    code = entry.main(["validate-config", "--config", str(bad)])
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_validate_config_rejects_matcher_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    bad_matcher = tmp_path / "matcher.yaml"
    # rules non-list → MatcherConfigError (structural parse)
    bad_matcher.write_text("tokens: {}\nrules: {}\n", encoding="utf-8")
    argv = [
        "validate-config",
        "--config",
        str(_write_config(tmp_path)),
        "--targets",
        str(_CONFIG / "targets.yml"),
        "--matcher",
        str(bad_matcher),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err


def test_validate_config_rejects_config_error_in_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AMULE_API_PASSWORD", "s3cr3t")
    bad_targets = tmp_path / "targets.yaml"
    bad_targets.write_text("episodes: nope\n", encoding="utf-8")  # episodes non-list → ConfigError
    argv = [
        "validate-config",
        "--config",
        str(_write_config(tmp_path)),
        "--targets",
        str(bad_targets),
        "--matcher",
        str(_CONFIG / "matcher.yml"),
    ]
    code = entry.main(argv)
    assert code == 1
    assert "Invalid config" in capsys.readouterr().err

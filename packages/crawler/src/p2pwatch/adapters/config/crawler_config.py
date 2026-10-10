"""UNIFIED crawler config (``crawler.yml``, versioned — deploy-simplification design).

Merges the former POLICY config (cadences, polling budgets, jitter, backoff, shutdown
deadline) and the former LOCAL config (secrets + DB paths + download/port-sync wiring). Parsed
from the dict ``load_yaml`` returns into FROZEN dataclasses, with FAIL-FAST validation: a bound
that does not hold or a missing field is a ``ConfigError`` and the crawler refuses to start.

Deployment-sensitive values (secrets, URLs) are interpolated from the environment via
``${NAME}`` (substring, LAZY: a disabled section requires no variable, D1). The
``download`` and ``port_sync`` sections are present ⟺ enabled (``enabled: true``, D5):
``enabled`` absent/``false`` ⇒ section ``None`` (we don't descend into the rest); ``enabled:
true`` ⇒ all wiring fields required.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from p2pwatch.adapters.config.errors import ConfigError as ConfigError  # explicit re-export
from p2pwatch.adapters.config.interpolation import interpolate
from p2pwatch.domain.observability.policy import Audience


@dataclass(frozen=True)
class BackoffConfig:
    """Exponential backoff + jitter per (instance, channel) (spec §3/§5).

    ``jitter_ratio``: fraction of the nominal delay drawn as additional jitter
    (anti-thundering-herd) — 0 = no jitter, 0.3 = up to +30%.
    """

    base_seconds: float
    cap_seconds: float
    factor: float
    jitter_ratio: float


@dataclass(frozen=True)
class AmuleEndpoint:
    """An ``amuled`` daemon reachable through ``amuleapi``. ``name`` is the instance label
    (logging, backoff/scheduler_state key)."""

    name: str
    host: str
    port: int
    password: str


AMULE_API_HOST = "127.0.0.1"
AMULE_API_PORT = 4711
AMULE_INSTANCE_NAME = "amuled"


@dataclass(frozen=True)
class NotificationTarget:
    """An apprise target (secret via ``${...}``). ``tag`` = the consuming audience (E-D7).
    ``node_prefix: false`` drops the ``[node-id]`` prefix, for a service with its own identity."""

    url: str
    tag: Audience
    node_prefix: bool = True


@dataclass(frozen=True)
class DownloadConfig:
    """Download policy and wiring, present ⟺ ``enabled``. ``output_dir`` is measured with
    ``statvfs`` and never opened: amuled writes the finished file, nothing here touches it."""

    poll_interval_seconds: float
    min_free_bytes: int
    lost_after_seconds: float
    output_dir: str


@dataclass(frozen=True)
class PortSyncConfig:
    """High-ID port-sync policy + wiring (port-sync design §8.1). Present ⟺ ``enabled``.

    ``poll_interval_seconds``: cadence of the gluetun poll + port comparison.
    ``restart_min_interval_seconds``: rate-limit window for restarts.
    ``gluetun_control_url`` = gluetun control-server (forwarded port). The restart itself needs
    no URL any more: amuled is a local s6 service (``S6MuleRestarter``, design §9).
    """

    poll_interval_seconds: float
    restart_min_interval_seconds: float
    gluetun_control_url: str


@dataclass(frozen=True)
class WebuiConfig:
    """In-process read-only webui HTTP surface (monolith-consolidation spec §8).

    ``enabled`` gates the WHOLE HTTP surface (``false`` ⇒ headless crawler, no port). The section
    is OPTIONAL (absent ⇒ enabled). The uvicorn bind is FIXED at ``0.0.0.0:8080`` in the
    composition layer, not configurable here; exposure is governed by the operator's Docker
    compose (published port + networks), not by an app-level bind address.

    ``amule_url`` is where the nav's aMule link points. The container publishes two web surfaces
    (design §9) and p2pwatch cannot know how its own is reached, so this is a plain configurable
    base: the default is the no-proxy case, and an operator behind a reverse proxy overrides it.
    """

    enabled: bool
    amule_url: str = "http://localhost:4711"


_DEFAULT_WEBUI = WebuiConfig(enabled=True)

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Keys no longer read: refused, so an operator never believes they still apply.
_REMOVED_KEYS = (
    "cycle_interval_seconds",
    "keyword_pause_max_seconds",
    "keyword_pause_min_seconds",
    "search_poll_budget_seconds",
    "search_poll_interval_seconds",
)


@dataclass(frozen=True)
class MetricsConfig:
    """Crawler's Prometheus metrics server (E-D9). ``port`` = dedicated HTTP server."""

    enabled: bool
    port: int


@dataclass(frozen=True)
class ObservabilityConfig:
    """Observability settings (``crawler.yml``). ``log_level`` drives the global logging
    (bootstrap → setLevel); ``notifications`` = apprise targets (interpolated URLs, E-D2/E-D7)."""

    log_level: str
    metrics: MetricsConfig | None
    notification_timeout_seconds: float
    notifications: tuple[NotificationTarget, ...]


@dataclass(frozen=True)
class CrawlerConfig:
    """Unified crawler config (policy + wiring). All durations in SECONDS.

    Policy: ``backoff``, ``decision_poll_interval_seconds`` (nudge safety net),
    ``shutdown_deadline_seconds`` (hard bound of the clean shutdown).

    Wiring (ex-local): ``amule_api_password`` (the daemon's amuleapi admin password, the same
    one the aMule web UI takes; host/port are code
    constants), DB paths, ``node_id`` (``None`` = the one from ``local.db``), ``observability``,
    ``download`` (``None`` ⟺ observer mode), ``port_sync`` (``None`` ⟺ port-sync off).

    ``search_keywords``: keywords queried by the search loop (``search`` section
    optional; default ``("keroro", "titar")`` if absent).
    """

    backoff: BackoffConfig
    decision_poll_interval_seconds: float
    shutdown_deadline_seconds: float
    amule_api_password: str
    catalog_db_path: str
    local_db_path: str
    node_id: str | None
    search_keywords: tuple[str, ...] = ("keroro", "titar")
    observability: ObservabilityConfig | None = None
    download: DownloadConfig | None = None
    port_sync: PortSyncConfig | None = None
    webui: WebuiConfig = _DEFAULT_WEBUI

    @property
    def amule_endpoint(self) -> AmuleEndpoint:
        """The single daemon's amuleapi endpoint: code constants + the configured password.

        ONE derivation point for all three sessions (search, download, port-sync).
        """
        return AmuleEndpoint(
            name=AMULE_INSTANCE_NAME,
            host=AMULE_API_HOST,
            port=AMULE_API_PORT,
            password=self.amule_api_password,
        )


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{what}: mapping expected, got {type(value).__name__}")
    return value


def _number(mapping: dict[str, Any], key: str, what: str) -> float:
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: number expected, got {value!r}")
    return float(value)


def _positive(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number <= 0:
        raise ConfigError(f"{what}.{key}: strictly positive expected, got {number}")
    return number


def _non_negative(mapping: dict[str, Any], key: str, what: str) -> float:
    number = _number(mapping, key, what)
    if number < 0:
        raise ConfigError(f"{what}.{key}: ≥ 0 expected, got {number}")
    return number


def _positive_int(mapping: dict[str, Any], key: str, what: str) -> int:
    """Strictly positive integer (bool refused), else ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{what}.{key}: strictly positive integer expected, got {value!r}")
    return value


def _bool(mapping: dict[str, Any], key: str, what: str) -> bool:
    """REQUIRED boolean, else ``ConfigError`` (fail-fast §5/§14)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: boolean expected, got {value!r}")
    return value


def _positive_default(mapping: dict[str, Any], key: str, default: float, what: str) -> float:
    """OPTIONAL strictly positive float (default ``default``)."""
    if key not in mapping:
        return default
    return _positive(mapping, key, what)


def _positive_int_default(mapping: dict[str, Any], key: str, default: int, what: str) -> int:
    """OPTIONAL strictly positive integer (default ``default``)."""
    if key not in mapping:
        return default
    return _positive_int(mapping, key, what)


def _bool_default(mapping: dict[str, Any], key: str, default: bool, what: str) -> bool:
    """OPTIONAL boolean (default ``default``); refuses a non-bool — fail-fast (§5/§14)."""
    if key not in mapping:
        return default
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key}: boolean expected, got {value!r}")
    return value


def _require_str(mapping: dict[str, Any], key: str, what: str, env: Mapping[str, str]) -> str:
    """Non-empty string, ``${NAME}``-interpolated from ``env`` AFTER reading, BEFORE the non-empty
    check (fail-fast §5/§14). Missing env variable ⇒ ``ConfigError`` (via ``interpolate``)."""
    if key not in mapping:
        raise ConfigError(f"{what}: key {key!r} missing")
    value = mapping[key]
    if not isinstance(value, str):
        raise ConfigError(f"{what}.{key}: non-empty string expected, got {value!r}")
    interpolated = interpolate(value, env, f"{what}.{key}")
    if not interpolated:
        raise ConfigError(f"{what}.{key}: non-empty string expected, got {interpolated!r}")
    return interpolated


def _parse_observability(raw: dict[str, Any], env: Mapping[str, str]) -> ObservabilityConfig:
    log_level = raw.get("log_level", "INFO")
    if not isinstance(log_level, str) or log_level not in _LOG_LEVELS:
        raise ConfigError(
            f"observability.log_level: one of {sorted(_LOG_LEVELS)} expected, got {log_level!r}"
        )
    metrics: MetricsConfig | None = None
    if "metrics" in raw:
        metrics_raw = _require_mapping(raw["metrics"], "observability.metrics")
        metrics = MetricsConfig(
            enabled=_bool(metrics_raw, "enabled", "observability.metrics"),
            port=_positive_int(metrics_raw, "port", "observability.metrics"),
        )
    timeout = (
        _positive(raw, "notification_timeout_seconds", "observability")
        if "notification_timeout_seconds" in raw
        else 5.0
    )
    notifications: list[NotificationTarget] = []
    for index, entry in enumerate(raw.get("notifications", [])):
        what = f"observability.notifications[{index}]"
        mapping = _require_mapping(entry, what)
        tag_raw = _require_str(mapping, "tag", what, env)
        try:
            tag = Audience(tag_raw)
        except ValueError as error:
            raise ConfigError(
                f"{what}.tag: 'community' or 'operations' expected, got {tag_raw!r}"
            ) from error
        notifications.append(
            NotificationTarget(
                url=_require_str(mapping, "url", what, env),
                tag=tag,
                node_prefix=_bool_default(mapping, "node_prefix", True, what),
            )
        )
    return ObservabilityConfig(
        log_level=log_level,
        metrics=metrics,
        notification_timeout_seconds=timeout,
        notifications=tuple(notifications),
    )


# The three download knobs of 2026-09-13 default rather than fail fast: an operator config
# written before them must still boot. 24 h absorbs an amuled restart or a night of downtime.
_DEFAULT_MIN_FREE_BYTES = 10_737_418_240  # 10 GiB
_DEFAULT_LOST_AFTER_SECONDS = 86_400.0
_DEFAULT_OUTPUT_DIR = "/downloads"  # the bind mount in deploy/base.compose.yml


def _parse_download(raw: dict[str, Any]) -> DownloadConfig | None:
    if "download" not in raw:
        return None
    section = _require_mapping(raw["download"], "section 'download'")
    if not _bool_default(section, "enabled", False, "download"):
        return None  # laziness: we read/interpolate NOTHING else (no variable required)
    output_dir = section.get("output_dir", _DEFAULT_OUTPUT_DIR)
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError(f"download.output_dir: non-empty string expected, got {output_dir!r}")
    return DownloadConfig(
        poll_interval_seconds=_positive(section, "poll_interval_seconds", "download"),
        min_free_bytes=_positive_int_default(
            section, "min_free_bytes", _DEFAULT_MIN_FREE_BYTES, "download"
        ),
        lost_after_seconds=_positive_default(
            section, "lost_after_seconds", _DEFAULT_LOST_AFTER_SECONDS, "download"
        ),
        output_dir=output_dir,
    )


def _parse_search_keywords(raw: dict[str, Any]) -> tuple[str, ...]:
    """`search.keywords`: list of non-empty keywords. Absent → default (keroro, titar)."""
    if "search" not in raw:
        return ("keroro", "titar")
    section = _require_mapping(raw["search"], "section 'search'")
    if "keywords" not in section:
        return ("keroro", "titar")
    keywords = section["keywords"]
    if not isinstance(keywords, list) or not keywords:
        raise ConfigError("search.keywords: non-empty list of strings expected")
    result: list[str] = []
    for entry in keywords:
        if not isinstance(entry, str) or not entry:
            raise ConfigError(f"search.keywords: non-empty string expected, got {entry!r}")
        result.append(entry)
    return tuple(result)


def _parse_port_sync(raw: dict[str, Any], env: Mapping[str, str]) -> PortSyncConfig | None:
    if "port_sync" not in raw:
        return None
    section = _require_mapping(raw["port_sync"], "section 'port_sync'")
    if not _bool_default(section, "enabled", False, "port_sync"):
        return None  # laziness: we read/interpolate NOTHING else
    return PortSyncConfig(
        poll_interval_seconds=_positive(section, "poll_interval_seconds", "port_sync"),
        restart_min_interval_seconds=_positive(
            section, "restart_min_interval_seconds", "port_sync"
        ),
        gluetun_control_url=_require_str(section, "gluetun_control_url", "port_sync", env),
    )


def _parse_webui(raw: dict[str, Any], env: Mapping[str, str]) -> WebuiConfig:
    """`webui` section (optional). Absent ⇒ enabled, default aMule link.

    The bind is FIXED at 0.0.0.0:8080 in the composition layer, so this reads no host/port: an
    unknown key (a legacy ``host``/``port``) is ignored silently, no fail-fast."""
    if "webui" not in raw:
        return _DEFAULT_WEBUI
    section = _require_mapping(raw["webui"], "section 'webui'")
    enabled = _bool_default(section, "enabled", True, "webui")
    if "amule_url" not in section:
        return WebuiConfig(enabled=enabled)
    return WebuiConfig(enabled=enabled, amule_url=_require_str(section, "amule_url", "webui", env))


def parse_crawler_config(raw: dict[str, Any], env: Mapping[str, str]) -> CrawlerConfig:
    """Builds a validated ``CrawlerConfig`` from the parsed YAML dict + the ``env`` environment
    (interpolation of ``${NAME}``). Fail-fast §5/§14: any inconsistency → ``ConfigError``."""
    for key in _REMOVED_KEYS:
        if key in raw:
            raise ConfigError(f"crawler: key '{key}' was removed, delete it from crawler.yml")
    backoff_raw = _require_mapping(raw.get("backoff", {}), "section 'backoff'")
    factor = _positive(backoff_raw, "factor", "backoff")
    if factor < 1:
        raise ConfigError(f"backoff.factor must be ≥ 1 (growth), got {factor}")
    backoff = BackoffConfig(
        base_seconds=_positive(backoff_raw, "base_seconds", "backoff"),
        cap_seconds=_positive(backoff_raw, "cap_seconds", "backoff"),
        factor=factor,
        jitter_ratio=_non_negative(backoff_raw, "jitter_ratio", "backoff"),
    )
    if backoff.cap_seconds < backoff.base_seconds:
        raise ConfigError(
            f"backoff.cap_seconds ({backoff.cap_seconds}) < base_seconds "
            f"({backoff.base_seconds}): cap below floor"
        )
    node_id_raw = raw.get("node_id")
    if node_id_raw is not None and (not isinstance(node_id_raw, str) or not node_id_raw):
        raise ConfigError(f"node_id: non-empty string or absent expected, got {node_id_raw!r}")
    observability: ObservabilityConfig | None = None
    if "observability" in raw:
        observability = _parse_observability(
            _require_mapping(raw["observability"], "section 'observability'"), env
        )
    return CrawlerConfig(
        backoff=backoff,
        decision_poll_interval_seconds=_positive(raw, "decision_poll_interval_seconds", "crawler"),
        shutdown_deadline_seconds=_positive(raw, "shutdown_deadline_seconds", "crawler"),
        amule_api_password=_require_str(raw, "amule_api_password", "crawler", env),
        catalog_db_path=_require_str(raw, "catalog_db_path", "crawler", env),
        local_db_path=_require_str(raw, "local_db_path", "crawler", env),
        node_id=node_id_raw,
        search_keywords=_parse_search_keywords(raw),
        observability=observability,
        download=_parse_download(raw),
        port_sync=_parse_port_sync(raw, env),
        webui=_parse_webui(raw, env),
    )

from typing import Any

import pytest

from p2pwatch.adapters.config.crawler_config import (
    AmuleEndpoint,
    BackoffConfig,
    ConfigError,
    CrawlerConfig,
    DownloadConfig,
    MetricsConfig,
    NotificationTarget,
    ObservabilityConfig,
    WebuiConfig,
    parse_crawler_config,
)
from p2pwatch.domain.observability.policy import Audience


def _minimal_raw() -> dict[str, Any]:
    """Valid policy + minimal wiring (EC password without ${}, base paths) — observer mode."""
    return {
        "backoff": {
            "base_seconds": 2.0,
            "cap_seconds": 300.0,
            "factor": 2.0,
            "jitter_ratio": 0.3,
        },
        "decision_poll_interval_seconds": 5.0,
        "shutdown_deadline_seconds": 10.0,
        "amule_api_password": "secret",
        "catalog_db_path": "/data/catalog.db",
        "local_db_path": "/data/local.db",
    }


def _env() -> dict[str, str]:
    return {"AMULE_API_PASSWORD": "s3cr3t"}


def _full_download_section() -> dict[str, Any]:
    return {
        "enabled": True,
        "poll_interval_seconds": 30.0,
        "min_free_bytes": 1_000_000_000,
        "lost_after_seconds": 3600.0,
        "output_dir": "/data/out",
    }


# --------------------------------------------------------------------- policy


def test_parses_a_valid_config() -> None:
    config = parse_crawler_config(_minimal_raw(), _env())
    assert config == CrawlerConfig(
        backoff=BackoffConfig(base_seconds=2.0, cap_seconds=300.0, factor=2.0, jitter_ratio=0.3),
        decision_poll_interval_seconds=5.0,
        shutdown_deadline_seconds=10.0,
        amule_api_password="secret",
        catalog_db_path="/data/catalog.db",
        local_db_path="/data/local.db",
        node_id=None,
        observability=None,
        download=None,
    )


def test_jitter_ratio_zero_is_accepted() -> None:
    raw = _minimal_raw()
    raw["backoff"]["jitter_ratio"] = 0.0  # 0 = no jitter (≥ 0 allowed)
    assert parse_crawler_config(raw, _env()).backoff.jitter_ratio == 0.0


def test_negative_jitter_ratio_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["jitter_ratio"] = -0.1
    with pytest.raises(ConfigError, match="≥ 0 expected"):
        parse_crawler_config(raw, _env())


def test_missing_key_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["decision_poll_interval_seconds"]
    with pytest.raises(ConfigError, match="decision_poll_interval_seconds"):
        parse_crawler_config(raw, _env())


def test_non_numeric_value_is_fatal() -> None:
    raw = _minimal_raw()
    raw["decision_poll_interval_seconds"] = "souvent"
    with pytest.raises(ConfigError, match="number expected"):
        parse_crawler_config(raw, _env())


def test_bool_is_not_accepted_as_a_number() -> None:
    raw = _minimal_raw()
    raw["decision_poll_interval_seconds"] = True
    with pytest.raises(ConfigError, match="number expected"):
        parse_crawler_config(raw, _env())


def test_non_positive_value_is_fatal() -> None:
    raw = _minimal_raw()
    raw["decision_poll_interval_seconds"] = 0
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


@pytest.mark.parametrize(
    "key",
    [
        "cycle_interval_seconds",
        "keyword_pause_max_seconds",
        "keyword_pause_min_seconds",
        "port_sync",
        "search_poll_budget_seconds",
        "search_poll_interval_seconds",
    ],
)
def test_a_removed_key_is_refused_by_name(key: str) -> None:
    raw = _minimal_raw() | {key: 5.0}
    with pytest.raises(ConfigError, match=f"key '{key}' was removed"):
        parse_crawler_config(raw, _env())


def test_backoff_section_must_be_a_mapping() -> None:
    raw = _minimal_raw()
    raw["backoff"] = [1, 2, 3]
    with pytest.raises(ConfigError, match="section 'backoff'"):
        parse_crawler_config(raw, _env())


def test_backoff_factor_below_one_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["factor"] = 0.5
    with pytest.raises(ConfigError, match="factor must be ≥ 1"):
        parse_crawler_config(raw, _env())


def test_backoff_cap_below_base_is_fatal() -> None:
    raw = _minimal_raw()
    raw["backoff"]["cap_seconds"] = 1.0
    raw["backoff"]["base_seconds"] = 10.0
    with pytest.raises(ConfigError, match="cap below floor"):
        parse_crawler_config(raw, _env())


# ------------------------------------------------------- the single amuled (single container)


def test_node_id_override_is_kept() -> None:
    raw = _minimal_raw()
    raw["node_id"] = "fixed-node"
    assert parse_crawler_config(raw, _env()).node_id == "fixed-node"


def test_endpoint_is_derived_from_code_constants_and_the_password() -> None:
    config = parse_crawler_config(_minimal_raw(), _env())
    assert config.amule_endpoint == AmuleEndpoint(
        name="amuled", host="ed2k", port=4711, password="secret"
    )


def test_missing_amule_api_password_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["amule_api_password"]
    with pytest.raises(ConfigError, match="amule_api_password"):
        parse_crawler_config(raw, _env())


def test_empty_amule_api_password_is_fatal() -> None:
    raw = _minimal_raw() | {"amule_api_password": ""}
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())


def test_non_string_amule_api_password_is_fatal() -> None:
    raw = _minimal_raw() | {"amule_api_password": 1234}
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())


def test_leftover_pool_keys_are_ignored() -> None:
    # The live-node migration edits crawler.yml by hand (design §11). An operator who forgets to
    # delete the old pool keys must still get a bootable crawler, not a fail-fast on a dead key.
    raw = _minimal_raw() | {
        "amules": [{"name": "amule-1", "host": "amuled", "port": 4712, "password": "x"}],
    }
    config = parse_crawler_config(raw, _env())
    assert config.amule_endpoint.host == "ed2k"


def test_missing_db_path_is_fatal() -> None:
    raw = _minimal_raw()
    del raw["catalog_db_path"]
    with pytest.raises(ConfigError, match="catalog_db_path"):
        parse_crawler_config(raw, _env())


def test_empty_node_id_string_is_fatal() -> None:
    raw = _minimal_raw()
    raw["node_id"] = ""
    with pytest.raises(ConfigError, match="node_id"):
        parse_crawler_config(raw, _env())


# ----------------------------------------------------------- interpolation ${}


def test_password_interpolated_from_env() -> None:
    # The domain never reads the environment: the adapter resolves ${NAME} before anything else
    # sees the value (design §6).
    raw = _minimal_raw() | {"amule_api_password": "${AMULE_API_PASSWORD}"}
    cfg = parse_crawler_config(raw, {"AMULE_API_PASSWORD": "s3cr3t"})
    assert cfg.amule_api_password == "s3cr3t"
    assert cfg.amule_endpoint.password == "s3cr3t"


def test_missing_env_var_raises() -> None:
    raw = _minimal_raw() | {"amule_api_password": "${AMULE_API_PASSWORD}"}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})  # AMULE_API_PASSWORD not set


# ----------------------------------------------------------------- download


def test_download_absent_is_observer() -> None:
    cfg = parse_crawler_config(_minimal_raw(), _env())
    assert cfg.download is None


def test_download_enabled_false_is_observer_without_requiring_wiring() -> None:
    # enabled:false ⇒ we do NOT read the rest: a missing poll_interval is NOT an error.
    raw = _minimal_raw() | {"download": {"enabled": False}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is None


def test_download_section_without_enabled_defaults_to_observer() -> None:
    # enabled missing → default false → download None (missing-key branch of _bool_default).
    raw = _minimal_raw() | {"download": {"poll_interval_seconds": 30.0}}
    assert parse_crawler_config(raw, _env()).download is None


def test_download_enabled_non_bool_is_fatal() -> None:
    raw = _minimal_raw() | {"download": {"enabled": "oui"}}
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_download_section_must_be_a_mapping() -> None:
    raw = _minimal_raw() | {"download": [1, 2]}
    with pytest.raises(ConfigError, match="section 'download'"):
        parse_crawler_config(raw, _env())


def test_download_enabled_true_requires_the_poll_interval() -> None:
    raw = _minimal_raw() | {"download": {"enabled": True, "min_free_bytes": 1024}}
    with pytest.raises(ConfigError, match="poll_interval_seconds"):
        parse_crawler_config(raw, _env())


def test_download_enabled_true_full_is_download_mode() -> None:
    raw = _minimal_raw() | {"download": _full_download_section()}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download == DownloadConfig(
        poll_interval_seconds=30.0,
        min_free_bytes=1_000_000_000,
        lost_after_seconds=3600.0,
        output_dir="/data/out",
    )


def test_download_poll_interval_must_be_positive() -> None:
    section = _full_download_section() | {"poll_interval_seconds": 0.0}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_download_min_free_must_be_positive_integer() -> None:
    section = _full_download_section() | {"min_free_bytes": 0}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_download_lost_after_must_be_positive() -> None:
    section = _full_download_section() | {"lost_after_seconds": 0.0}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="strictly positive"):
        parse_crawler_config(raw, _env())


def test_download_space_and_ttl_knobs_have_defaults() -> None:
    # All three are new in 2026-09-13: an operator config predating them must still boot, so
    # they default instead of failing fast like the older required keys.
    section = _full_download_section()
    for key in ("min_free_bytes", "lost_after_seconds", "output_dir"):
        del section[key]
    raw = _minimal_raw() | {"download": section}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.download is not None
    assert cfg.download.min_free_bytes == 10_737_418_240  # 10 GiB
    assert cfg.download.lost_after_seconds == 86_400.0  # 24 h
    assert cfg.download.output_dir == "/downloads"  # what base.compose.yml mounts


def test_download_output_dir_must_be_a_string() -> None:
    section = _full_download_section() | {"output_dir": 42}
    raw = _minimal_raw() | {"download": section}
    with pytest.raises(ConfigError, match="output_dir"):
        parse_crawler_config(raw, _env())


# ------------------------------------------------------------------- search


def test_search_keywords_defaults_to_keroro_and_titar_when_section_absent() -> None:
    config = parse_crawler_config(_minimal_raw(), {})
    assert config.search_keywords == ("keroro", "titar")


def test_search_keywords_defaults_when_section_present_without_keywords_key() -> None:
    raw = _minimal_raw()
    raw["search"] = {}
    config = parse_crawler_config(raw, {})
    assert config.search_keywords == ("keroro", "titar")


def test_search_keywords_read_from_section() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", "titar", "mission titar"]}
    config = parse_crawler_config(raw, {})
    assert config.search_keywords == ("keroro", "titar", "mission titar")


def test_search_keywords_rejects_empty_list() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": []}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})


def test_search_keywords_rejects_non_string_entry() -> None:
    raw = _minimal_raw()
    raw["search"] = {"keywords": ["keroro", 42]}
    with pytest.raises(ConfigError):
        parse_crawler_config(raw, {})


# ----------------------------------------------------------- observability


def test_observability_absent_defaults_to_none() -> None:
    assert parse_crawler_config(_minimal_raw(), _env()).observability is None


def test_observability_parsed() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "DEBUG",
            "metrics": {"enabled": True, "port": 9100},
            "notification_timeout_seconds": 3.0,
        }
    }
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability == ObservabilityConfig(
        log_level="DEBUG",
        metrics=MetricsConfig(enabled=True, port=9100),
        notification_timeout_seconds=3.0,
        notifications=(),
    )


def test_observability_metrics_optional() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO"}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability == ObservabilityConfig(
        log_level="INFO", metrics=None, notification_timeout_seconds=5.0, notifications=()
    )


def test_observability_bad_log_level_rejected() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "LOUD"}}
    with pytest.raises(ConfigError, match="log_level"):
        parse_crawler_config(raw, _env())


def test_observability_metrics_enabled_key_missing_rejected() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO", "metrics": {"port": 9100}}}
    with pytest.raises(ConfigError, match="'enabled' missing"):
        parse_crawler_config(raw, _env())


def test_observability_metrics_port_key_missing_rejected() -> None:
    # missing-key branch of _positive_int (the only required caller left since the download
    # knobs gained defaults).
    raw = _minimal_raw() | {"observability": {"log_level": "INFO", "metrics": {"enabled": True}}}
    with pytest.raises(ConfigError, match="port"):
        parse_crawler_config(raw, _env())


def test_observability_metrics_enabled_non_bool_rejected() -> None:
    raw = _minimal_raw() | {
        "observability": {"log_level": "INFO", "metrics": {"enabled": 1, "port": 9100}}
    }
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_notifications_absent_is_empty() -> None:
    raw = _minimal_raw() | {"observability": {"log_level": "INFO"}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability is not None
    assert cfg.observability.notifications == ()


def test_notifications_parsed() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "INFO",
            "notifications": [
                {"url": "discord://a", "tag": "community", "node_prefix": False},
                {"url": "discord://b", "tag": "operations"},
            ],
        }
    }
    cfg = parse_crawler_config(raw, _env())
    assert cfg.observability is not None
    assert cfg.observability.notifications == (
        NotificationTarget(url="discord://a", tag=Audience.COMMUNITY, node_prefix=False),
        NotificationTarget(url="discord://b", tag=Audience.OPERATIONS, node_prefix=True),
    )


def test_notification_node_prefix_non_bool_rejected() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "INFO",
            "notifications": [{"url": "x", "tag": "community", "node_prefix": "no"}],
        }
    }
    with pytest.raises(ConfigError, match=r"notifications\[0\]\.node_prefix: boolean expected"):
        parse_crawler_config(raw, _env())


def test_notifications_bad_tag_rejected() -> None:
    raw = _minimal_raw() | {
        "observability": {"log_level": "INFO", "notifications": [{"url": "x", "tag": "nope"}]}
    }
    with pytest.raises(ConfigError, match="tag"):
        parse_crawler_config(raw, _env())


def test_notification_url_interpolated_substring() -> None:
    raw = _minimal_raw() | {
        "observability": {
            "log_level": "INFO",
            "notifications": [{"url": "discord://${WID}/${WTOK}", "tag": "operations"}],
        }
    }
    cfg = parse_crawler_config(raw, _env() | {"WID": "1", "WTOK": "t"})
    assert cfg.observability is not None
    assert cfg.observability.notifications[0].url == "discord://1/t"


# ------------------------------------------------------------------- webui


def test_webui_absent_defaults_to_enabled() -> None:
    # No webui section → the in-process webui is ON by default. The bind is fixed at
    # 0.0.0.0:8080 in the composition layer; it is NOT configurable here.
    cfg = parse_crawler_config(_minimal_raw(), _env())
    assert cfg.webui == WebuiConfig(enabled=True)


def test_webui_enabled_false_disables_the_surface() -> None:
    raw = _minimal_raw() | {"webui": {"enabled": False}}
    assert parse_crawler_config(raw, _env()).webui == WebuiConfig(enabled=False)


def test_webui_host_and_port_are_silently_ignored() -> None:
    # A legacy config still carrying host/port must NOT fail: those keys are simply not read
    # (the bind is fixed in code). The result carries only `enabled`, nothing else.
    raw = _minimal_raw() | {"webui": {"enabled": True, "host": "1.2.3.4", "port": 9999}}
    cfg = parse_crawler_config(raw, _env())
    assert cfg.webui == WebuiConfig(enabled=True)
    assert not hasattr(cfg.webui, "host")  # the type no longer carries a bind
    assert not hasattr(cfg.webui, "port")


def test_webui_enabled_defaults_true_when_key_absent_in_section() -> None:
    # Section present (with a now-ignored host) but no `enabled` key → default True
    # (missing-key branch of _bool_default); the host is ignored.
    raw = _minimal_raw() | {"webui": {"host": "1.2.3.4"}}
    assert parse_crawler_config(raw, _env()).webui == WebuiConfig(enabled=True)


def test_webui_section_must_be_a_mapping() -> None:
    raw = _minimal_raw() | {"webui": [1, 2]}
    with pytest.raises(ConfigError, match="section 'webui'"):
        parse_crawler_config(raw, _env())


def test_webui_enabled_non_bool_is_fatal() -> None:
    raw = _minimal_raw() | {"webui": {"enabled": "yes"}}
    with pytest.raises(ConfigError, match="boolean expected"):
        parse_crawler_config(raw, _env())


def test_webui_amule_url_defaults_to_the_published_port() -> None:
    # Both web surfaces are published by default (design §9): p2pwatch on 8080, amuleweb on
    # 4711. The default is the no-proxy case; anything else is the operator's to set.
    assert parse_crawler_config(_minimal_raw(), _env()).webui.amule_url == "http://localhost:4711"


def test_webui_amule_url_is_configurable_for_a_reverse_proxy() -> None:
    # The link must survive a reverse proxy in front of 8080, which is the whole point of the key.
    raw = _minimal_raw() | {"webui": {"amule_url": "https://mule.example.org/amule"}}
    assert parse_crawler_config(raw, _env()).webui.amule_url == "https://mule.example.org/amule"


def test_webui_amule_url_is_interpolated_from_env() -> None:
    raw = _minimal_raw() | {"webui": {"amule_url": "${AMULE_UI_URL}"}}
    cfg = parse_crawler_config(raw, _env() | {"AMULE_UI_URL": "http://nas.lan:4711"})
    assert cfg.webui.amule_url == "http://nas.lan:4711"


def test_webui_amule_url_empty_is_fatal() -> None:
    raw = _minimal_raw() | {"webui": {"amule_url": ""}}
    with pytest.raises(ConfigError, match="non-empty string"):
        parse_crawler_config(raw, _env())

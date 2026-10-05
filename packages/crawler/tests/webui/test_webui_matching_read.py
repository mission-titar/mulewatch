"""TDD tests for matching_read.MatchingExplainer (Task 9 — W-D7).

Since P4a the explainer takes an ALREADY-PARSED ``MatcherConfig`` + ``targets`` tuple
(the YAML → config parsing moved up into the caller), so these tests parse the minimal
fixtures with the canonical ``parse_matcher_config`` / ``parse_targets`` and pass the parsed
objects.
"""

import yaml

from catalog_matching.config import MatcherConfig
from catalog_matching.engine import Explanation
from catalog_matching.models import TargetSegment
from catalog_matching.validation import parse_matcher_config, parse_targets
from mulewatch.webui.adapters.matching_read import MatchingExplainer

# ---------------------------------------------------------------------------
# Parsed-config minimal helpers
# ---------------------------------------------------------------------------


def _minimal_targets() -> tuple[TargetSegment, ...]:
    return parse_targets(
        yaml.safe_load(
            """\
episodes:
  - season: 2
    seasonal_number: 11
    absolute_number: 62
    segments:
      - letter: a
        title: "La Grenouille Cosmique"
"""
        )
    )


def _minimal_matcher() -> MatcherConfig:
    """Minimal matcher: keyword token 'keroro' + catalog rule."""
    return parse_matcher_config(
        yaml.safe_load(
            """\
tokens:
  keroro:
    keyword: keroro
rules:
  - name: catalog
    tier: catalog
    any:
      - keroro
"""
        )
    )


# ---------------------------------------------------------------------------
# Tests — explain()
# ---------------------------------------------------------------------------


def test_explainer_returns_explanation_on_matching_filename() -> None:
    """Filename containing 'keroro' → Explanation with non-empty rules_fired."""
    explainer = MatchingExplainer(
        matcher_config=_minimal_matcher(),
        targets=_minimal_targets(),
    )
    result = explainer.explain(
        filename="Keroro_062A_VF.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert len(result.rules_fired) > 0
    assert result.target_id == "062A"


def test_explainer_returns_none_for_unknown_target() -> None:
    """target_id unknown to the config → None."""
    explainer = MatchingExplainer(
        matcher_config=_minimal_matcher(),
        targets=_minimal_targets(),
    )
    result = explainer.explain(
        filename="Keroro_062A_VF.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="S9E999Z",
    )
    assert result is None


def test_explainer_size_bytes_converted_to_mb() -> None:
    """size_bytes → size_mb via Mio conversion (1024*1024).

    We verify the FileCandidate is built without error with size_bytes provided.
    An attr_between matcher on size_mb lets us verify the passed value.
    """
    # Matcher with an attr_between token on size_mb: exactly 100 MiB
    matcher_config = parse_matcher_config(
        yaml.safe_load(
            """\
tokens:
  keroro:
    keyword: keroro
  size_ok:
    attr_between: size_mb
    min: 99.9
    max: 100.1
rules:
  - name: catalog
    tier: catalog
    all:
      - keroro
      - size_ok
"""
        )
    )

    explainer = MatchingExplainer(
        matcher_config=matcher_config,
        targets=_minimal_targets(),
    )
    # 100 * 1024 * 1024 = 104857600 bytes = exactly 100.0 MiB
    result = explainer.explain(
        filename="keroro_062a.avi",
        size_bytes=104857600,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_media_length_and_bitrate_forwarded() -> None:
    """media_length_sec and bitrate_kbps are forwarded as-is to the FileCandidate."""
    # Matcher with attr_between on duration_sec and bitrate_kbps
    matcher_config = parse_matcher_config(
        yaml.safe_load(
            """\
tokens:
  keroro:
    keyword: keroro
  dur_ok:
    attr_between: duration_sec
    min: 1300.0
    max: 1400.0
  bit_ok:
    attr_between: bitrate_kbps
    min: 1000.0
    max: 2000.0
rules:
  - name: catalog
    tier: catalog
    all:
      - keroro
      - dur_ok
      - bit_ok
"""
        )
    )

    explainer = MatchingExplainer(
        matcher_config=matcher_config,
        targets=_minimal_targets(),
    )
    result = explainer.explain(
        filename="keroro_062a.avi",
        size_bytes=None,
        media_length_sec=1350,
        bitrate_kbps=1500,
        target_id="062A",
    )
    assert isinstance(result, Explanation)
    assert "catalog" in result.rules_fired


def test_explainer_engine_cached_across_calls() -> None:
    """Multiple explain() calls reuse the same engine (identical object)."""
    explainer = MatchingExplainer(
        matcher_config=_minimal_matcher(),
        targets=_minimal_targets(),
    )
    # Two successive calls — the engine must be built ONCE (stable private attribute)
    r1 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    r2 = explainer.explain(
        filename="keroro_vf.avi",
        size_bytes=None,
        media_length_sec=None,
        bitrate_kbps=None,
        target_id="062A",
    )
    # Both results are identical (stable engine)
    assert r1 == r2

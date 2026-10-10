"""Data guardrail: the PROD catalog (deploy/targets.yml) loads and
respects the expected invariants (180 S1+S2 targets, contiguous numbering, 17 recovered).

Cf. spec 2026-06-30-targets-keroro-dual-numbering §6. The 26 mono episodes have only one
target (segment A); the bi-segment episodes have two.
"""

from pathlib import Path

from catalog_matching.validation import parse_targets
from p2pwatch.adapters.config.yaml_loader import load_yaml

_TARGETS = Path(__file__).resolve().parents[4] / "deploy" / "targets.yml"

# Recovered segments (cf. spec §6): 17 in total.
_FOUND = {
    "001A",
    "001B",
    "002A",
    "002B",
    "003A",
    "003B",
    "004A",
    "004B",
    "005A",
    "005B",
    "006A",
    "006B",
    "010A",  # mono recovered
    "027B",  # "Station thermale à gogo !" (segment B), A lost
    "036B",  # "La station de ski privée des Bellair" (segment B), A lost
    "062A",  # S02E11A (absolute 62), B lost
    "103A",  # S02E52 (absolute 103), mono recovered
}


def test_prod_targets_load_180_unique_segments() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert len(targets) == 180
    assert len({t.target_id for t in targets}) == 180


def test_prod_targets_segment_counts_per_season() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert sum(t.season == 1 for t in targets) == 91
    assert sum(t.season == 2 for t in targets) == 89


def test_prod_targets_absolute_numbering_is_contiguous_1_to_103() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    assert {t.absolute_number for t in targets} == set(range(1, 104))


def test_prod_targets_seasonal_relationship_holds() -> None:
    # seasonal_number is load-bearing for the seasonal forms (S2E11A / 2x11A) and the
    # bare-number mono token: we lock the relation absolute = offset + seasonal and the
    # seasonal contiguity, so that a mistranscribed seasonal_number (with a valid
    # absolute) does not silently route the seasonal form to the wrong episode.
    targets = parse_targets(load_yaml(_TARGETS))
    for t in targets:
        offset = 0 if t.season == 1 else 51
        assert t.absolute_number == offset + t.seasonal_number, t.target_id
    assert sorted({t.seasonal_number for t in targets if t.season == 1}) == list(range(1, 52))
    assert sorted({t.seasonal_number for t in targets if t.season == 2}) == list(range(1, 53))


def test_prod_targets_found_segments_match_recovered_list() -> None:
    targets = parse_targets(load_yaml(_TARGETS))
    found = {t.target_id for t in targets if t.status == "found"}
    assert found == _FOUND

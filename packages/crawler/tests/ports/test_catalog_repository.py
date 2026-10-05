from collections.abc import Iterator

from catalog_matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    Explanation,
    MatchDecision,
)
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.catalog_repository import CatalogRepository, ObservedFile, ReevalRow


class _StubRepository:
    """Minimal structural implementation: satisfies CatalogRepository WITHOUT importing it."""

    def __init__(self) -> None:
        self.observations: list[FileObservation] = []
        self.decisions: list[tuple[str, MatchDecision]] = []
        self.retractions: list[tuple[str, str]] = []

    def record_observation(self, observation: FileObservation) -> None:
        self.observations.append(observation)

    def record_decision(self, ed2k_hash: str, decision: MatchDecision) -> None:
        self.decisions.append((ed2k_hash, decision))

    def record_retraction(self, ed2k_hash: str, target_id: str) -> None:
        self.retractions.append((ed2k_hash, target_id))

    def last_decisions(self, ed2k_hash: str) -> dict[str, DecisionRecord]:
        return {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return ()

    def last_observation(self, ed2k_hash: str) -> ObservedFile | None:
        return None

    def known_filenames(self, ed2k_hash: str) -> tuple[str, ...]:
        return ()

    def count_files(self) -> int:
        return 1

    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]:
        return iter(
            (
                ReevalRow(
                    ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
                    filename="Keroro 062A.avi",
                    size_bytes=100,
                    media_length_sec=None,
                    bitrate_kbps=None,
                ),
            )
        )


def test_protocol_is_satisfied_structurally() -> None:
    stub = _StubRepository()
    repository: CatalogRepository = stub  # mypy proves the structural satisfaction
    observation = FileObservation(
        ed2k_hash="31d6cfe0d16ae931b73c59d7e0c089c0",
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
        complete_source_count=0,
        keyword="keroro",
    )
    decision = MatchDecision(
        target_id="062A",
        rule_name="exact",
        tier="download",
        explanation=Explanation(
            target_id="062A", rules_fired=("exact",), tokens_matched=(), coverage_values=()
        ),
    )
    repository.record_observation(observation)
    repository.record_decision(observation.ed2k_hash, decision)
    repository.record_retraction(observation.ed2k_hash, "062A")
    assert repository.last_decisions(observation.ed2k_hash) == {}
    assert repository.download_decisions() == ()
    assert repository.last_observation(observation.ed2k_hash) is None
    assert repository.known_filenames(observation.ed2k_hash) == ()
    assert repository.count_files() == 1
    assert tuple(repository.iter_reevaluation_rows()) == (
        ReevalRow(
            ed2k_hash=observation.ed2k_hash,
            filename=observation.filename,
            size_bytes=observation.size_bytes,
            media_length_sec=None,
            bitrate_kbps=None,
        ),
    )
    assert stub.observations == [observation]
    assert stub.decisions == [(observation.ed2k_hash, decision)]
    assert stub.retractions == [(observation.ed2k_hash, "062A")]

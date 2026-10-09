from collections.abc import Iterator

from catalog_matching.engine import (
    DecisionRecord,
    DownloadCandidate,
    Explanation,
    MatchDecision,
)
from mulewatch.domain.file_key import FileKey, Network
from mulewatch.domain.observation import FileObservation
from mulewatch.ports.catalog_repository import CatalogRepository, ObservedFile, ReevalRow

_KEY = FileKey(Network.ED2K, "31d6cfe0d16ae931b73c59d7e0c089c0")


class _StubRepository:
    """Minimal structural implementation: satisfies CatalogRepository WITHOUT importing it."""

    def __init__(self) -> None:
        self.observations: list[FileObservation] = []
        self.decisions: list[tuple[FileKey, MatchDecision]] = []
        self.retractions: list[tuple[FileKey, str]] = []

    def record_observation(self, observation: FileObservation) -> None:
        self.observations.append(observation)

    def record_decision(self, file: FileKey, decision: MatchDecision) -> None:
        self.decisions.append((file, decision))

    def record_retraction(self, file: FileKey, target_id: str) -> None:
        self.retractions.append((file, target_id))

    def last_decisions(self, file: FileKey) -> dict[str, DecisionRecord]:
        return {}

    def download_decisions(self) -> tuple[DownloadCandidate, ...]:
        return ()

    def last_observation(self, file: FileKey) -> ObservedFile | None:
        return None

    def best_observation(self, file: FileKey) -> ObservedFile | None:
        return None

    def known_filenames(self, file: FileKey) -> tuple[str, ...]:
        return ()

    def count_files(self) -> int:
        return 1

    def iter_reevaluation_rows(self) -> Iterator[ReevalRow]:
        return iter(
            (
                ReevalRow(
                    file=_KEY,
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
        file=_KEY,
        filename="Keroro 062A.avi",
        size_bytes=100,
        source_count=1,
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
    repository.record_decision(_KEY, decision)
    repository.record_retraction(_KEY, "062A")
    assert repository.last_decisions(_KEY) == {}
    assert repository.download_decisions() == ()
    assert repository.last_observation(_KEY) is None
    assert repository.best_observation(_KEY) is None
    assert repository.known_filenames(_KEY) == ()
    assert repository.count_files() == 1
    assert tuple(repository.iter_reevaluation_rows()) == (
        ReevalRow(
            file=_KEY,
            filename=observation.filename,
            size_bytes=observation.size_bytes,
            media_length_sec=None,
            bitrate_kbps=None,
        ),
    )
    assert stub.observations == [observation]
    assert stub.decisions == [(_KEY, decision)]
    assert stub.retractions == [(_KEY, "062A")]

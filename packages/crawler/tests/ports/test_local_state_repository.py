from p2pwatch.ports.local_state_repository import LocalStateRepository


class _StubRepository:
    """Minimal structural implementation: satisfies LocalStateRepository WITHOUT importing it."""

    def node_id(self) -> str:
        return "00000000-0000-0000-0000-000000000000"

    def last_backfill_policy(self) -> str | None:
        return None

    def set_last_backfill_policy(self, sha256: str) -> None:
        return None


def test_protocol_is_satisfied_structurally() -> None:
    repository: LocalStateRepository = _StubRepository()  # mypy proves the satisfaction
    assert repository.node_id() == "00000000-0000-0000-0000-000000000000"
    assert repository.last_backfill_policy() is None
    repository.set_last_backfill_policy("abc")

from p2pwatch.domain.download.states import DownloadState, is_terminal


def test_states_are_a_closed_enum() -> None:
    assert set(DownloadState) == {
        DownloadState.QUEUED,
        DownloadState.DOWNLOADING,
        DownloadState.COMPLETED,
        DownloadState.FAILED,
    }


def test_state_values_are_stable_strings() -> None:
    assert DownloadState.QUEUED.value == "queued"
    assert DownloadState.COMPLETED.value == "completed"


def test_terminal_states_do_not_consume_active_quota() -> None:
    assert is_terminal(DownloadState.COMPLETED) is True
    assert is_terminal(DownloadState.FAILED) is True


def test_active_states_are_not_terminal() -> None:
    assert is_terminal(DownloadState.QUEUED) is False
    assert is_terminal(DownloadState.DOWNLOADING) is False

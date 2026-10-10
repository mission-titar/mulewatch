"""EdgeState: first_occurrence = transition to active; leave rearms."""

from p2pwatch.application.edge_state import EdgeState


def test_enter_is_true_only_on_transition() -> None:
    state = EdgeState()
    assert state.enter("verifier_unavailable") is True  # 1st time → transition
    assert state.enter("verifier_unavailable") is False  # already active → no re-notify


def test_leave_rearms() -> None:
    state = EdgeState()
    state.enter("blind")
    assert state.leave("blind") is True  # was active → exit transition
    assert state.leave("blind") is False  # already inactive
    assert state.enter("blind") is True  # rearmed → re-transition


def test_conditions_are_independent() -> None:
    state = EdgeState()
    assert state.enter("a") is True
    assert state.enter("b") is True
    assert state.enter("a") is False

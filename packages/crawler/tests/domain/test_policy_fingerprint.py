from p2pwatch.domain.policy_fingerprint import policy_fingerprint


def test_fingerprint_is_deterministic_for_identical_bytes() -> None:
    matcher_bytes = b"matcher: rules"
    targets_bytes = b"targets: list"
    assert policy_fingerprint(matcher_bytes, targets_bytes) == policy_fingerprint(
        matcher_bytes, targets_bytes
    )


def test_fingerprint_differs_when_matcher_bytes_differ() -> None:
    targets_bytes = b"targets: list"
    first = policy_fingerprint(b"matcher: v1", targets_bytes)
    second = policy_fingerprint(b"matcher: v2", targets_bytes)
    assert first != second


def test_fingerprint_differs_when_targets_bytes_differ() -> None:
    matcher_bytes = b"matcher: rules"
    first = policy_fingerprint(matcher_bytes, b"targets: v1")
    second = policy_fingerprint(matcher_bytes, b"targets: v2")
    assert first != second


def test_fingerprint_is_not_ambiguous_under_concatenation() -> None:
    # A byte moved from the end of `matcher` to the start of `targets` must NOT collide:
    # the length prefix guards against a naive `matcher_bytes + targets_bytes` concat.
    first = policy_fingerprint(b"matcherX", b"targets")
    second = policy_fingerprint(b"matcher", b"Xtargets")
    assert first != second

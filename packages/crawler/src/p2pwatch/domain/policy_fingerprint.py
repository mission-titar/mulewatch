"""Policy fingerprint: gate for the startup catalogue re-evaluation (spec §7.1).

PURE domain: ``hashlib.sha256`` over bytes GIVEN by the caller — no file I/O here (the
caller, ``composition/__main__.py``, reads ``matcher.yml``/``targets.yml`` and passes
their raw bytes). Both files feed ``MatchingEngine`` (a target/title edit changes
decisions too), so the fingerprint spans both, in a fixed order.
"""

import hashlib


def policy_fingerprint(matcher_bytes: bytes, targets_bytes: bytes) -> str:
    """sha256 hex over both policy files' bytes (matcher then targets).

    The length of ``matcher_bytes`` is prefixed (8 bytes, big-endian) before the
    concatenation: without it, a byte moved from the end of one file to the start of
    the other would produce an IDENTICAL digest (``a + b == a[:-1] + (a[-1:] + b)``).
    """
    digest = hashlib.sha256(len(matcher_bytes).to_bytes(8, "big") + matcher_bytes + targets_bytes)
    return digest.hexdigest()

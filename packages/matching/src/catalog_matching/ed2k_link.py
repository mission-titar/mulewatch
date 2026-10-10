"""PURE ed2k link construction (shared crawler + webui domain).

The link has the form ``ed2k://|file|<name>|<size>|<hash>|/`` (format consumed by
``EC_OP_ADD_LINK`` on the crawler side, reproduced for copy/share on the webui side). The
``|`` is the FIELD SEPARATOR: a hostile filename could, if it contained a ``|``, inject a
field and break the link framing (size/hash shifted → link unusable or pointing elsewhere).
So we escape the name with UTF-8 percent-encoding (``urllib.parse.quote``), keeping a
readable safe set — a space becomes ``%20``, the ``|`` becomes ``%7C``, control characters
and non-ASCII are neutralized. Only the 5 STRUCTURAL separators of the link
(``|file|`` … ``|/``) stay as ``|``.

PURE domain: no I/O. Lives in ``catalog_matching`` (shared package) rather than in
``p2pwatch`` core or ``p2pwatch.webui`` because both must produce the SAME canonical
link for a given file (regression webui-security#0 — without this sharing, the webui
reinvented the function and forgot the escaping).
"""

from urllib.parse import quote

# Set kept UNescaped: readable and safe (no space, no ``|``, no control char). The rest
# goes through percent-encoding (a space → ``%20``, the ed2k canon expected by the test).
# ``/`` is NOT in the safe set (a name is never a path here).
_SAFE_NAME_CHARS = ".()[]-_"


def build_ed2k_link(filename: str, size_bytes: int, ed2k_hash: str) -> str:
    """ed2k link for a file. The name is escaped (``|`` → ``%7C``, etc.)."""
    safe_name = quote(filename, safe=_SAFE_NAME_CHARS)
    return f"ed2k://|file|{safe_name}|{size_bytes}|{ed2k_hash}|/"

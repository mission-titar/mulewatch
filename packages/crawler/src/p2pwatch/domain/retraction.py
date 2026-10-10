"""Retraction: the sentinel tier recorded when a file stops matching a target (spec §7).

PURE domain. ``catalog.db``'s ``match_decisions`` table is append-only (DB triggers) — there
is no "delete" primitive for a stale decision. When the current matcher policy no longer
matches a previously-catalogued file for a given target, exclusion is represented as an
*appended* row carrying THAT ``target_id``: ``rule_name=""``, ``tier=RETRACTED_TIER``.
Retraction is PER TARGET: a whole-episode file can retract ``(hash, 072A)`` while leaving
``(hash, 072B)`` intact. (The legacy ``target_id=""`` sentinel — a whole-file retraction —
is no longer written; it survives in old catalogs and is simply ignored by the read side.)

``RETRACTED_TIER`` is deliberately NOT a member of ``catalog_matching.config.TIERS``
(``{"catalog", "notify", "download"}``): the matching engine never produces it — it is
synthesized by the crawler alone, on the "matched → no longer matched" transition.
"""

RETRACTED_TIER = "retracted"

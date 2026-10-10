"""SQLite persistence adapter: the two databases of MVP spec §11/§12 (data-model spec §4).

``catalog.db`` (append-only, content-addressed, ready to merge) and ``local.db``
(operational, never merged). ``sqlite3`` stdlib, SYNCHRONOUS repositories, hand-written
SQL (spec §3); ``.sql`` migrations embedded in ``migrations/`` (read via
``importlib.resources``), linted by sqlfluff at the gate.
"""

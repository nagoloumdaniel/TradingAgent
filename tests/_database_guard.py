"""Which database a test URL may point at, decided without connecting to anything.

The rule these tests encode: a test suite that drops every table must never be aimed at the
agent's own database. The original guard demanded a database name ending in `_test`, which
is a good signal but not the actual requirement — an operator who provisions a *second*
Supabase project gets a database called `postgres`, and refusing it protects nothing while
making the PostgreSQL tests unrunnable.

So the guard now asks the real question: is this demonstrably a different database from
`DATABASE_URL`? The `_test` suffix remains accepted, because it is a deliberate signal.
"""

from sqlalchemy import make_url
from sqlalchemy.engine import URL

# A database name that announces itself is always accepted.
SUFFIX = "_test"


def same_database(left: URL, right: URL) -> bool:
    """True when both URLs target the same host, port and database name."""
    return (
        (left.host or "").lower(),
        left.port,
        (left.database or "").lower(),
    ) == (
        (right.host or "").lower(),
        right.port,
        (right.database or "").lower(),
    )


def guard_test_database(url: str, production_url: str | None) -> str:
    """Return ``url`` when a test suite may safely drop its tables, raise otherwise.

    Raises ``ValueError`` with the reason; the caller turns it into a pytest usage error.
    """
    target = make_url(url)
    database = target.database or ""

    if database.lower().endswith(SUFFIX):
        return url

    if not production_url:
        raise ValueError(
            f"TEST_DATABASE_URL names {database!r}, which does not end in {SUFFIX!r}, and "
            "DATABASE_URL is unknown: there is no way to tell it apart from the agent's "
            "database. These tests drop every table."
        )

    if same_database(target, make_url(production_url)):
        raise ValueError(
            f"TEST_DATABASE_URL points at the agent's own database "
            f"({target.host}/{database}): these tests drop every table. Point it at a "
            "separate project, or rename the database to end in _test."
        )

    return url

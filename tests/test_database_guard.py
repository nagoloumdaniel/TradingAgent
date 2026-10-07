"""The rule that keeps a table-dropping test suite away from the agent's database.

Tested directly because the failure it prevents is unrecoverable, and because the guard was
once too strict in a way that made the PostgreSQL tests unrunnable on a perfectly safe
setup: a second Supabase project, whose database is called `postgres` like everyone else's.
"""

import pytest
from tests._database_guard import guard_test_database, same_database

PRODUCTION = "postgresql://u:p@db.production.supabase.co:5432/postgres"
SEPARATE = "postgresql://u:p@db.second-project.supabase.co:5432/postgres"


def test_a_name_ending_in_test_is_always_accepted() -> None:
    url = "postgresql://u:p@db.production.supabase.co:5432/tradingagent_test"
    assert guard_test_database(url, PRODUCTION) == url


def test_a_separate_database_is_accepted_without_the_suffix() -> None:
    """The case that motivated the change: a dedicated Supabase project, named `postgres`."""
    assert guard_test_database(SEPARATE, PRODUCTION) == SEPARATE


def test_the_agents_own_database_is_refused() -> None:
    with pytest.raises(ValueError, match="agent's own database"):
        guard_test_database(PRODUCTION, PRODUCTION)


def test_the_refusal_names_the_offending_target() -> None:
    with pytest.raises(ValueError) as caught:
        guard_test_database(PRODUCTION, PRODUCTION)
    assert "db.production.supabase.co" in str(caught.value)


def test_the_same_host_with_a_different_database_is_accepted() -> None:
    """A second database on the same server is a normal development setup."""
    neighbour = "postgresql://u:p@db.production.supabase.co:5432/autre_base"
    assert guard_test_database(neighbour, PRODUCTION) == neighbour


def test_the_same_name_on_a_different_host_is_accepted() -> None:
    assert guard_test_database(SEPARATE, PRODUCTION) == SEPARATE


def test_a_different_port_is_a_different_database() -> None:
    """Host and port identify the server; the name identifies the database inside it."""
    local = "postgresql://u:p@db.production.supabase.co:5433/postgres"
    assert guard_test_database(local, PRODUCTION) == local


def test_without_a_production_url_the_suffix_is_required() -> None:
    """No reference point means no way to prove the target is safe: demand the signal."""
    with pytest.raises(ValueError, match="does not end in '_test'"):
        guard_test_database(SEPARATE, None)
    accepted = "postgresql://u:p@db.second-project.supabase.co:5432/anything_test"
    assert guard_test_database(accepted, None) == accepted


def test_the_comparison_ignores_case_and_missing_parts() -> None:
    from sqlalchemy import make_url

    upper = make_url("postgresql://u:p@DB.Example.COM:5432/Trading")
    lower = make_url("postgresql://u:p@db.example.com:5432/trading")
    assert same_database(upper, lower)
    assert not same_database(upper, make_url("postgresql://u:p@db.example.com:5432/other"))


def test_the_error_explains_what_to_do() -> None:
    with pytest.raises(ValueError) as caught:
        guard_test_database(SEPARATE, None)
    message = str(caught.value)
    assert "drop every table" in message
    assert "_test" in message

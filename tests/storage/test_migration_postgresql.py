"""Guard against a defect only PostgreSQL could see — checked without a PostgreSQL server.

Migration `0006` declared a `CHECK` constraint explicitly *and* let its `Enum` column emit
one with the same name. SQLite accepts two constraints sharing a name; PostgreSQL refuses
the whole `CREATE TABLE`. The suite stayed green because the PostgreSQL tests need
`TEST_DATABASE_URL` and were skipped, and the deployment could not migrate at all.

Rendering the chain offline would be the ideal check, but Alembic reaches for the
PostgreSQL dialect's default schema and ends up opening a connection — which is exactly
what a test must not need. These two guards are static, instant and deterministic, and one
of them fails on the exact shape of the original bug.
"""

import ast
from pathlib import Path

from tradingagent.storage.models import Base

MIGRATIONS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "tradingagent"
    / "storage"
    / "migrations"
    / "versions"
)


def _call_name(node: ast.AST) -> str | None:
    """`sa.Enum` -> 'sa.Enum', `op.f` -> 'op.f'."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        value = node.func.value
        if isinstance(value, ast.Name):
            return f"{value.id}.{node.func.attr}"
    return None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _creates_constraint(node: ast.Call) -> bool:
    """True only when the enum is told to emit its own CHECK.

    `0001` writes `create_constraint=False` and declares the constraint by hand; `0006`
    wrote `create_constraint=True` *and* declared it by hand. The keyword being present is
    not the question — its value is.
    """
    flag = _keyword(node, "create_constraint")
    return isinstance(flag, ast.Constant) and flag.value is True


def _string(node: ast.AST | None) -> str | None:
    """A literal, or the string inside `op.f("...")` — Alembic's naming-convention call."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call) and _call_name(node) == "op.f" and node.args:
        return _string(node.args[0])
    return None


def _constraint_names(table: str, call: ast.Call) -> list[str]:
    """Every constraint name the naming convention would give this table.

    The whole call is walked, not just its positional arguments: the `Enum` carrying the
    first name sits *inside* a `sa.Column`, which is precisely where the duplicate hid.
    """
    names: list[str] = []
    for node in ast.walk(call):
        if not isinstance(node, ast.Call):
            continue
        called = _call_name(node)
        if called == "sa.CheckConstraint":
            explicit = _string(_keyword(node, "name"))
            if explicit:
                names.append(explicit)
        elif called == "sa.Enum" and _creates_constraint(node):
            enum_name = _string(_keyword(node, "name"))
            if enum_name:
                # `ck_%(table_name)s_%(constraint_name)s`, with the enum name as the
                # constraint name — the same string an explicit constraint would use.
                names.append(f"ck_{table}_{enum_name}")
    return names


def _tables(node: ast.AST) -> list[tuple[str, ast.Call]]:
    found: list[tuple[str, ast.Call]] = []
    for child in ast.walk(node):
        if _call_name(child) == "op.create_table" and isinstance(child, ast.Call):
            table = _string(child.args[0]) if child.args else None
            if table:
                found.append((table, child))
    return found


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.py"))


def test_every_migration_parses() -> None:
    files = _migration_files()
    assert len(files) >= 6, f"expected the whole chain, found {len(files)}"
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_create_table_declares_a_constraint_twice() -> None:
    """The regression this file exists for.

    `0006` gave `strategy_registry` two constraints named
    `ck_strategy_registry_strategystatus`: one from the `Enum`, one written by hand.
    """
    collisions: list[str] = []
    for path in _migration_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for table, call in _tables(tree):
            names = _constraint_names(table, call)
            seen: set[str] = set()
            for name in names:
                if name in seen:
                    collisions.append(f"{path.name}:{table}:{name}")
                seen.add(name)
    assert collisions == []


def test_the_collision_detector_actually_detects(ast_self=None) -> None:
    """A guard nobody has seen fail is not a guard: feed it the original defect."""
    source = """
op.create_table(
    "strategy_registry",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column(
        "status",
        sa.Enum("discovered", "live", name="strategystatus", create_constraint=True),
        nullable=False,
    ),
    sa.CheckConstraint("status IN ('discovered', 'live')",
                       name=op.f("ck_strategy_registry_strategystatus")),
)
"""
    tree = ast.parse(source)
    tables = _tables(tree)
    assert len(tables) == 1
    names = _constraint_names(*tables[0])
    assert names.count("ck_strategy_registry_strategystatus") == 2


def test_the_models_declare_each_constraint_once() -> None:
    """The other half of the same rule, on the metadata `create_all` would use."""
    for table in Base.metadata.tables.values():
        # Built in a loop, not a comprehension: SQLAlchemy types an unnamed constraint's
        # `name` as a private `_NoneName` sentinel, and `isinstance` is what narrows it.
        names: list[str] = []
        for constraint in table.constraints:
            name = constraint.name
            if isinstance(name, str) and name:
                names.append(name)
        duplicates = sorted({name for name in names if names.count(name) > 1})
        assert duplicates == [], f"{table.name}: {duplicates}"


def test_the_v3_tables_are_declared_in_the_models() -> None:
    for table in (
        "strategy_registry",
        "backtest_runs",
        "validation_runs",
        "ai_analyses",
        "ai_proposals",
        "execution_events",
        "daily_performance",
    ):
        assert table in Base.metadata.tables, table

"""SQLite connections and ordered, atomic schema migrations."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import current_app, g

Record = dict[str, Any]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(Path(current_app.config["DATABASE"]))
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@contextmanager
def transaction():
    """Serialize mutations; nested services compose using a savepoint."""
    connection = get_db()
    nested = connection.in_transaction
    if nested:
        from uuid import uuid4
        savepoint = "sp_" + uuid4().hex
        connection.execute(f"SAVEPOINT {savepoint}")
    else:
        connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException as error:
        if nested:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.rollback()
        if isinstance(error,sqlite3.IntegrityError) and 'project_retired' in str(error):
            from onpf.errors import DomainError
            raise DomainError('project_retired','This project is retired. Reactivate privately before changing its documentation.',409) from None
        raise
    else:
        if nested:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.commit()


def apply_migrations(connection: sqlite3.Connection, names: list[str]) -> None:
    """Apply trusted ordered packaged filenames in the caller's transaction.

    This executor neither begins, commits nor rolls back. Archive callers must
    validate their supported schema allowlist before passing names here.
    """
    if not connection.in_transaction:
        raise RuntimeError('Applying migrations requires a caller-owned transaction')
    packaged = {source.name: source for source in (Path(__file__).parent / 'migrations').glob('*.sql')}
    if any(not isinstance(name, str) or name not in packaged for name in names):
        raise ValueError('Choose trusted packaged migration filenames')
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    for name in names:
        if name in applied:
            continue
        # executescript commits open transactions. Execute complete statements instead.
        statement = ""
        for line in packaged[name].read_text(encoding="utf-8-sig").splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)", (name, utcnow()))
        applied.add(name)


def migrate(path: Path) -> None:
    """Apply all pending packaged migrations in one transaction."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        names = [source.name for source in sorted((Path(__file__).parent / "migrations").glob("*.sql"))]
        apply_migrations(connection, names)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_login_required(client):
    assert client.get("/workspace").status_code == 302


def test_csrf_required(client, owner):
    assert client.post("/login", data={"username": "owner", "password": "fictional-owner-password"}).status_code == 400


def test_wrong_program_denied(app_context, owner, other_owner):
    from onpf.auth.service import require_role
    from onpf.db import get_db
    from onpf.errors import DomainError
    conn = get_db()
    conn.execute("INSERT INTO programs(id,title,created_at,updated_at) VALUES ('program-a','Fictional A','2026-09-26T00:00:00+00:00','2026-09-26T00:00:00+00:00')")
    conn.execute("INSERT INTO memberships(program_id,user_id,role) VALUES ('program-a',?,'owner')", (owner.user_id,))
    require_role(owner, "program-a", {"owner"})
    with pytest.raises(DomainError) as error:
        require_role(other_owner, "program-a", {"owner"})
    assert error.value.status == 403


def test_restart_preserves_account(app, owner):
    from onpf.app import create_app
    from onpf.auth.service import authenticate
    restarted = create_app({"TESTING": True, "DATABASE": app.config["DATABASE"], "INSTANCE_PATH": app.instance_path})
    with restarted.app_context():
        assert authenticate("owner", "fictional-owner-password").user_id == owner.user_id
    assert restarted.secret_key == app.secret_key


def test_password_is_hashed(app_context, owner):
    from onpf.db import get_db
    from werkzeug.security import check_password_hash
    stored = get_db().execute("SELECT password_hash FROM users WHERE id=?", (owner.user_id,)).fetchone()[0]
    assert "fictional-owner-password" not in stored
    assert check_password_hash(stored, "fictional-owner-password")


def test_login_creates_hashed_durable_session(app, login_client, owner):
    from onpf.db import get_db
    token = login_client.get_cookie("onpf_session").value
    with app.app_context():
        rows = get_db().execute("SELECT token_hash,user_id FROM auth_sessions").fetchall()
    assert len(rows) == 1
    assert rows[0][0] != token
    assert rows[0][0] == hashlib.sha256(token.encode()).hexdigest()
    assert rows[0][1] == owner.user_id
    assert login_client.get("/workspace").status_code == 200


def test_session_survives_restart(app, login_client):
    from onpf.app import create_app
    restarted = create_app({"TESTING": True, "DATABASE": app.config["DATABASE"], "INSTANCE_PATH": app.instance_path})
    fresh_client = restarted.test_client()
    fresh_client.set_cookie("onpf_session", login_client.get_cookie("onpf_session").value)
    assert fresh_client.get("/workspace").status_code == 200


def test_expired_session_requires_login(app, login_client):
    from onpf.db import get_db
    with app.app_context():
        get_db().execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00+00:00'")
    assert login_client.get("/workspace").status_code == 302


def test_logout_revokes_replayed_session(app, login_client, csrf_token):
    old_token = login_client.get_cookie("onpf_session").value
    token = csrf_token(login_client, "/workspace")
    assert login_client.post("/logout", data={"csrf_token": token}).status_code == 302
    attacker = app.test_client()
    attacker.set_cookie("onpf_session", old_token)
    assert attacker.get("/workspace").status_code == 302


def test_logout_requires_csrf(login_client):
    assert login_client.post("/logout").status_code == 400
    assert login_client.get("/workspace").status_code == 200


def test_cookie_flags(client, owner, csrf_token):
    response = client.post("/login", data={"username": "owner", "password": "fictional-owner-password", "csrf_token": csrf_token(client)})
    auth_cookie = next(value for value in response.headers.getlist("Set-Cookie") if value.startswith("onpf_session="))
    assert "HttpOnly" in auth_cookie
    assert "SameSite=Lax" in auth_cookie


def test_login_throttling_is_persistent(app, owner):
    from onpf.auth.service import authenticate
    from onpf.errors import DomainError
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        with app.app_context(), pytest.raises(DomainError) as error:
            authenticate("owner", "incorrect-password")
        assert error.value.status == 403
    with app.app_context(), pytest.raises(DomainError) as error:
        authenticate("owner", "fictional-owner-password")
    assert error.value.status == 429


def test_login_validation_preserves_username(client, csrf_token):
    response = client.post("/login", data={"username": "<fictional-name>", "password": "incorrect-password", "csrf_token": csrf_token(client)})
    assert response.status_code == 403
    assert "&lt;fictional-name&gt;" in response.text
    assert "incorrect-password" not in response.text


def test_untrusted_role_and_invite_cannot_authorize(app_context, owner):
    from onpf.auth.models import Principal
    from onpf.auth.service import require_role
    from onpf.errors import DomainError
    with pytest.raises(DomainError) as error:
        require_role(Principal(None, "fake-invite", "fake-batch"), "missing-program", {"owner"})
    assert error.value.status == 403


def test_cli_requires_secure_password_input(tmp_path):
    init = subprocess.run([sys.executable, "-m", "onpf.cli", "--instance", str(tmp_path / "cli"), "init"], text=True, capture_output=True)
    assert init.returncode == 0, init.stderr
    result = subprocess.run([sys.executable, "-m", "onpf.cli", "--instance", str(tmp_path / "cli"), "create-user", "--username", "example", "--password-stdin"], input="", text=True, capture_output=True)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    database = tmp_path / "cli" / "onpf.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as conn:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_cli_init_and_user(tmp_path):
    instance = tmp_path / "cli"
    base = [sys.executable, "-m", "onpf.cli", "--instance", str(instance)]
    result = subprocess.run(base + ["init"], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(instance / "onpf.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    result = subprocess.run(base + ["create-user", "--username", "example", "--password-stdin"], input="fictional-cli-password\n", text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "fictional-cli-password" not in result.stdout + result.stderr


def test_foreign_keys_enabled(app_context):
    from onpf.db import get_db
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute("INSERT INTO memberships(program_id,user_id,role) VALUES ('missing','missing','owner')")


def test_migration_failure_is_atomic(tmp_path):
    from onpf.db import migrate
    path = tmp_path / "broken.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE memberships(dummy TEXT)")
    with pytest.raises(sqlite3.Error):
        migrate(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='programs'").fetchone() is None
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0


def test_connection_migrations_leave_commit_and_rollback_to_caller(tmp_path):
    from onpf import db
    apply = getattr(db, 'apply_migrations', None)
    assert callable(apply), 'Packaged migrations need one shared connection-level executor'
    connection = db.connect(tmp_path / 'caller.sqlite3')
    try:
        connection.execute('BEGIN IMMEDIATE')
        apply(connection, ['001_core.sql'])
        assert connection.in_transaction
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
        assert connection.execute('SELECT version FROM schema_migrations').fetchone()[0] == '001_core.sql'
        connection.rollback()
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
        connection.execute('BEGIN IMMEDIATE')
        apply(connection, ['001_core.sql'])
        apply(connection, ['001_core.sql', '002_programs.sql'])
        assert [row[0] for row in connection.execute('SELECT version FROM schema_migrations ORDER BY rowid')] == ['001_core.sql', '002_programs.sql']
        connection.commit()
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='programs'").fetchone()
    finally:
        connection.close()


@pytest.mark.parametrize('names', [['../untrusted.sql'], ['001_core.sql', '999_unknown.sql']])
def test_connection_migrations_refuse_nonpackaged_names_before_writing(tmp_path, names):
    from onpf import db
    apply = getattr(db, 'apply_migrations', None)
    assert callable(apply), 'Packaged migrations need one shared connection-level executor'
    connection = db.connect(tmp_path / 'untrusted.sqlite3')
    try:
        connection.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='packaged'):
            apply(connection, names)
        assert connection.in_transaction
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
    finally:
        connection.rollback()
        connection.close()


def test_connection_migrations_require_caller_transaction(tmp_path):
    from onpf import db
    apply = getattr(db, 'apply_migrations', None)
    assert callable(apply), 'Packaged migrations need one shared connection-level executor'
    connection = db.connect(tmp_path / 'no-transaction.sqlite3')
    try:
        with pytest.raises(RuntimeError, match='transaction'):
            apply(connection, ['001_core.sql'])
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
    finally:
        connection.close()

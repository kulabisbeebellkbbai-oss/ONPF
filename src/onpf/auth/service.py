"""Account, membership and durable session services."""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from uuid import uuid4

from flask import current_app, g, redirect, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from onpf.auth.models import Principal
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError

# Equalize the expensive password check for nonexistent accounts.
_DUMMY_PASSWORD_HASH = generate_password_hash("unusable-" + secrets.token_hex(32))


def create_user(username: str, password: str) -> str:
    username = username.strip()
    if not username or len(username) > 100:
        raise DomainError("invalid_username", "Enter a username of 1 to 100 characters.", 422)
    if len(password) < 12 or len(password) > 1024:
        raise DomainError("invalid_password", "Use a password of 12 to 1024 characters.", 422)
    user_id = str(uuid4())
    password_hash = generate_password_hash(password)
    try:
        with transaction() as connection:
            first = connection.execute("SELECT NOT EXISTS(SELECT 1 FROM users)").fetchone()[0]
            connection.execute("INSERT INTO users(id,username,password_hash,created_at,is_admin) VALUES (?,?,?,?,?)", (user_id, username, password_hash, utcnow(), first))
    except sqlite3.IntegrityError as error:
        raise DomainError("username_exists", "That username already exists.", 409) from error
    return user_id


def authenticate(username: str, password: str) -> Principal:
    username = username.strip()
    if len(username) > 100 or len(password) > 1024:
        raise DomainError("invalid_credentials", "The username or password is incorrect.", 403)
    now = datetime.now(timezone.utc)
    invalid = False
    with transaction() as connection:
        attempt = connection.execute("SELECT failures,blocked_until FROM login_attempts WHERE username=?", (username,)).fetchone()
        if attempt and attempt["blocked_until"] > now.isoformat():
            raise DomainError("login_throttled", "Too many login attempts. Try again later.", 429)
        row = connection.execute("SELECT id,password_hash FROM users WHERE username=? AND active=1", (username,)).fetchone()
        valid = check_password_hash(row["password_hash"] if row else _DUMMY_PASSWORD_HASH, password)
        if not row or not valid:
            failures = (attempt["failures"] if attempt else 0) + 1
            # Clear stale failure counts after the throttle window.
            if attempt and attempt["blocked_until"] and attempt["blocked_until"] <= now.isoformat():
                failures = 1
            expiry = (now + timedelta(seconds=current_app.config["LOGIN_THROTTLE_SECONDS"])).isoformat()
            connection.execute("INSERT INTO login_attempts(username,failures,blocked_until) VALUES (?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until", (username, failures, expiry if failures >= current_app.config["LOGIN_MAX_ATTEMPTS"] else ""))
            invalid = True
        else:
            connection.execute("DELETE FROM login_attempts WHERE username=?", (username,))
            principal = Principal(row["id"], None, None)
    # Commit the failed attempt before raising an error.
    if invalid:
        raise DomainError("invalid_credentials", "The username or password is incorrect.", 403)
    return principal


def require_role(actor: Principal, program_id: str, allowed: set[str]) -> None:
    if not isinstance(actor, Principal) or not actor.user_id or actor.invite_id or actor.batch_id:
        raise DomainError("forbidden", "You do not have access to this program.", 403)
    if not get_db().execute('SELECT 1 FROM users WHERE id=? AND active=1',(actor.user_id,)).fetchone():
        raise DomainError('forbidden','Your account access has been removed.',403)
    row = get_db().execute("SELECT role FROM memberships WHERE program_id=? AND user_id=?", (program_id, actor.user_id)).fetchone()
    if row is None or row["role"] not in allowed:
        raise DomainError("forbidden", "You do not have access to this program.", 403)


def _account_row(actor: Principal):
    if not isinstance(actor, Principal) or not actor.user_id or actor.invite_id or actor.batch_id:
        raise DomainError("forbidden", "An authenticated account is required.", 403)
    row = get_db().execute("SELECT id,username,is_admin,can_create_projects FROM users WHERE id=? AND active=1", (actor.user_id,)).fetchone()
    if row is None:
        raise DomainError("forbidden", "An authenticated account is required.", 403)
    return row


def account_permissions(actor: Principal) -> dict:
    row = _account_row(actor)
    return {"is_admin": bool(row["is_admin"]), "can_create_projects": bool(row["can_create_projects"])}


def require_project_creation(actor: Principal) -> None:
    if not _account_row(actor)["can_create_projects"]:
        raise DomainError("forbidden", "Your account cannot create new projects.", 403)


def require_admin(actor: Principal) -> None:
    if not _account_row(actor)["is_admin"]:
        raise DomainError("forbidden", "Installation administration is required.", 403)


def set_project_creation(actor: Principal, user_id: str, allowed: bool) -> None:
    with transaction() as connection:
        require_admin(actor)
        if type(allowed) is not bool:
            raise DomainError("invalid_permission", "Choose whether this account can create projects.", 422)
        if not connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise DomainError("not_found", "This account is unavailable.", 404)
        connection.execute("UPDATE users SET can_create_projects=? WHERE id=?", (int(allowed), user_id))


def set_membership(actor: Principal, program_id: str, user_id: str, role: str) -> None:
    with transaction() as connection:
        account = _account_row(actor)
        program = connection.execute("SELECT id FROM programs WHERE id=?", (program_id,)).fetchone()
        if program is None:
            raise DomainError("not_found", "This program is unavailable.", 404)
        own = connection.execute("SELECT role FROM memberships WHERE program_id=? AND user_id=?", (program_id, actor.user_id)).fetchone()
        if not account["is_admin"] and (own is None or own["role"] != "owner"):
            raise DomainError("forbidden", "Only a project owner or installation admin can manage its access.", 403)
        if not connection.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise DomainError("not_found", "This account is unavailable.", 404)
        if role not in ("", "owner", "facilitator", "contributor", "viewer"):
            raise DomainError("invalid_role", "Choose a valid project access level.", 422)
        current = connection.execute("SELECT role FROM memberships WHERE program_id=? AND user_id=?", (program_id, user_id)).fetchone()
        if current and current["role"] == "owner" and role != "owner":
            remaining = connection.execute("SELECT COUNT(*) FROM memberships WHERE program_id=? AND role='owner' AND user_id!=?", (program_id, user_id)).fetchone()[0]
            if not remaining:
                raise DomainError("last_owner", "Keep at least one decision owner.", 422)
        if (current["role"] if current else "") == role:
            return
        connection.execute("DELETE FROM memberships WHERE program_id=? AND user_id=?", (program_id, user_id))
        if role:
            connection.execute("INSERT INTO memberships(program_id,user_id,role) VALUES (?,?,?)", (program_id, user_id, role))
        connection.execute("UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?", (utcnow(), program_id))


def management_state(actor: Principal) -> dict:
    account = _account_row(actor)
    if account["is_admin"]:
        programs = [dict(row) for row in get_db().execute("SELECT id,title FROM programs ORDER BY title")]
    else:
        programs = [dict(row) for row in get_db().execute("SELECT p.id,p.title FROM programs p JOIN memberships m ON m.program_id=p.id WHERE m.user_id=? AND m.role='owner' ORDER BY p.title", (actor.user_id,))]
        if not programs:
            raise DomainError("forbidden", "Project ownership or installation administration is required.", 403)
    ids = [program["id"] for program in programs]
    memberships = [dict(row) for row in get_db().execute("SELECT program_id,user_id,role FROM memberships WHERE program_id IN (" + ",".join("?" for _ in ids) + ") ORDER BY program_id,user_id", ids)] if ids else []
    users = [{**dict(row), "is_admin": bool(row["is_admin"]), "can_create_projects": bool(row["can_create_projects"])} for row in get_db().execute("SELECT id,username,is_admin,can_create_projects FROM users ORDER BY username")]
    return {"is_admin": bool(account["is_admin"]), "programs": programs, "users": users, "memberships": memberships}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(actor: Principal) -> str:
    if not actor.user_id or actor.invite_id or actor.batch_id:
        raise DomainError("forbidden", "An account is required.", 403)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with transaction() as connection:
        connection.execute("INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)", (token_hash(token), actor.user_id, now.isoformat(), (now + timedelta(seconds=current_app.config["AUTH_SESSION_SECONDS"])).isoformat()))
    return token


def revoke_session(token: str | None) -> None:
    if token:
        with transaction() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash(token),))


def get_principal() -> Principal:
    if "principal" not in g:
        token = request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])
        row = None
        if token and len(token) <= 256:
            row = get_db().execute("SELECT s.user_id FROM auth_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (token_hash(token), utcnow())).fetchone()
        g.principal = Principal(row["user_id"] if row else None, None, None)
    return g.principal


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_principal().user_id:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

"""Browser authentication forms and workspace landing page."""
from urllib.parse import urlsplit
from importlib.resources import files

from flask import Blueprint, current_app, g, redirect, render_template, request, session, url_for

from onpf.auth.service import account_permissions, authenticate, create_session, get_principal, login_required, revoke_session
from onpf.db import get_db
from onpf.errors import DomainError

bp = Blueprint("auth", __name__)


@bp.before_app_request
def refresh_identity():
    g.pop("principal", None)
    g.pop(current_app.config.get("WTF_CSRF_FIELD_NAME", "csrf_token"), None)


@bp.app_context_processor
def auth_context():
    actor = get_principal()
    permissions = account_permissions(actor) if actor.user_id else {"is_admin": False, "can_create_projects": False}
    can_manage = bool(actor.user_id and (permissions["is_admin"] or get_db().execute("SELECT 1 FROM memberships WHERE user_id=? AND role='owner' LIMIT 1", (actor.user_id,)).fetchone()))
    return {"principal": actor, "current_app": current_app, "can_create_projects": permissions["can_create_projects"], "can_manage": can_manage}


@bp.route("/")
def index():
    return redirect(url_for("auth.workspace"))


@bp.get("/license")
def license_page():
    text = files('onpf').joinpath('static/LICENSE.txt').read_text(encoding='utf-8')
    return render_template('license.html', license_text=text)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", username="", error=None)
    username = request.form.get("username", "")
    try:
        principal = authenticate(username, request.form.get("password", ""))
    except DomainError as error:
        return render_template("login.html", username=username, error=error), error.status
    destination = request.args.get("next", "")
    try:
        parsed = urlsplit(destination)
        safe = not parsed.scheme and not parsed.netloc and destination.startswith('/') and not destination.startswith('//') and '\\' not in destination and not any(ord(char) < 32 for char in destination)
    except ValueError:
        safe = False
    if not safe:
        destination = url_for("auth.workspace")
    revoke_session(request.cookies.get(current_app.config["AUTH_COOKIE_NAME"]))
    token = create_session(principal)
    session.clear()  # Rotate the CSRF state on authentication.
    response = redirect(destination)
    response.set_cookie(current_app.config["AUTH_COOKIE_NAME"], token,
                        max_age=current_app.config["AUTH_SESSION_SECONDS"], httponly=True,
                        samesite="Lax", secure=current_app.config["SESSION_COOKIE_SECURE"])
    return response


@bp.post("/logout")
def logout():
    revoke_session(request.cookies.get(current_app.config["AUTH_COOKIE_NAME"]))
    session.clear()
    response = redirect(url_for("auth.login"))
    response.delete_cookie(current_app.config["AUTH_COOKIE_NAME"])
    return response


@bp.get("/workspace")
@login_required
def workspace():
    programs = [dict(row) for row in get_db().execute("SELECT p.id,p.title,p.operating_status,m.role FROM programs p JOIN memberships m ON m.program_id=p.id WHERE m.user_id=? ORDER BY p.title", (get_principal().user_id,))]
    return render_template("base.html", programs=programs)

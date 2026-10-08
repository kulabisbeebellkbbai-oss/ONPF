"""Compatibility entry point for the separate administration application."""
from flask import Blueprint, redirect, request
from onpf.auth.service import get_principal, login_required, management_state

bp = Blueprint("management", __name__, url_prefix="/manage")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    management_state(get_principal())
    return redirect('/admin/', code=308 if request.method == 'POST' else 302)

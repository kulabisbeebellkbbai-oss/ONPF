"""Private operating-permission record and attachment routes."""
from io import BytesIO

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from onpf.auth.service import get_principal, login_required, require_role
from onpf.errors import DomainError
from onpf.permissions import service
from onpf.programs.service import EDIT_ROLES, get_program

bp = Blueprint("permissions", __name__)


@bp.after_request
def private_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.route("/programs/<program_id>/permissions", methods=["GET", "POST"])
@login_required
def index(program_id):
    actor = get_principal()
    require_role(actor, program_id, EDIT_ROLES)
    program = get_program(actor, program_id)
    error = None
    values = {}
    if request.method == "POST":
        keys = ("issuer", "effective_on", "expires_on", "scope", "conditions", "filed_on", "evidence_name")
        values = {key: request.form.get(key, "") for key in keys}
        values["copies"] = [
            {"key": key, "label": request.form.get(f"copy_{key}_label", label), "state": request.form.get(f"copy_{key}_state", "unknown"), "date": request.form.get(f"copy_{key}_date", "")}
            for key, label in service.COPY_DEFAULTS
        ]
        upload = request.files.get("evidence")
        evidence = upload.stream.read(service.MAX_EVIDENCE + 1) if upload and upload.filename else None
        if upload and upload.filename:
            values["evidence_name"] = upload.filename
        try:
            service.save_permission(actor, program_id, values, evidence)
            flash("Private operating-permission evidence saved as a new version.")
            return redirect(url_for("permissions.index", program_id=program_id))
        except DomainError as problem:
            error = problem
    return render_template("permissions/index.html", program=program, records=service.list_permissions(actor, program_id), readiness=service.permission_readiness(program_id), copy_defaults=service.COPY_DEFAULTS, values=values, is_owner=actor.user_id in program["owner_ids"], error=error), error.status if error else 200


@bp.get("/programs/<program_id>/permissions/<record_id>/download")
@login_required
def download(program_id, record_id):
    record = service.get_permission(get_principal(), record_id)
    if record["program_id"] != program_id:
        raise DomainError("not_found", "This permission record is unavailable here.", 404)
    if not record["evidence"]:
        raise DomainError("not_found", "No evidence file was attached to this version.", 404)
    return send_file(BytesIO(record["evidence"]), mimetype="application/octet-stream", as_attachment=True, download_name=record["evidence_name"], conditional=False)


@bp.post("/programs/<program_id>/permissions/<record_id>/revoke")
@login_required
def revoke(program_id, record_id):
    record = service.get_permission(get_principal(), record_id)
    if record["program_id"] != program_id:
        raise DomainError("not_found", "This permission record is unavailable here.", 404)
    service.revoke_permission(get_principal(), record_id, request.form.get("revoked_on", ""), request.form.get("reason", ""))
    flash("Permission revocation recorded.")
    return redirect(url_for("permissions.index", program_id=program_id))

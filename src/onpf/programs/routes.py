"""Accessible browser setup and section-based document editor."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from onpf.auth.service import account_permissions, get_principal, login_required, require_project_creation
from onpf.db import get_db, transaction
from onpf.errors import DomainError
from onpf.programs.framework import load_framework
from onpf.programs.service import create_program, get_program, list_programs, readiness, save_document, update_program

bp = Blueprint("programs", __name__, url_prefix="/programs")


def _accounts():
    return [dict(row) for row in get_db().execute("SELECT id,username FROM users ORDER BY username")]


def _revision():
    try:
        return int(request.form.get("expected_revision", ""))
    except ValueError:
        raise DomainError("invalid_revision", "Reload the form to obtain the current revision.", 422)


def _setup_payload():
    payload = {key: request.form.get(key, "") for key in ("title", "purpose", "local_context")}
    payload["module_keys"] = request.form.getlist("module_keys")
    payload["operating_status"] = request.form.get("operating_status", "pending")
    payload["approval_rule"] = request.form.get("approval_rule", "all")
    # Named account controls never accept a client-supplied actor role.
    if request.form.get("membership_editor"):
        payload["memberships"] = [{"user_id": account["id"], "role": request.form[f'role_{account["id"]}']} for account in _accounts() if request.form.get(f'role_{account["id"]}', "")]
    return payload


@bp.get("")
@login_required
def index():
    return render_template("programs/index.html", programs=list_programs(get_principal()), can_create_projects=account_permissions(get_principal())["can_create_projects"])


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    require_project_creation(get_principal())
    payload, error = {"operating_status": "pending", "approval_rule": "all", "module_keys": []}, None
    if request.method == "POST":
        payload = _setup_payload()
        try:
            program = create_program(get_principal(), payload)
            flash("Program created. Its documents are drafts and permission to operate is recorded separately.")
            return redirect(url_for("programs.workspace", program_id=program["id"]))
        except DomainError as problem:
            error = problem
    return render_template("programs/create.html", values=payload, framework=load_framework(), error=error, editing=False, accounts=_accounts(), member_roles={}), error.status if error else 200


@bp.post("/setup-working-programs")
@login_required
def setup_working_programs():
    require_project_creation(get_principal())
    with transaction():
        create_program(get_principal(), {"title": "Bridge creative and growing program", "module_keys": ["art", "hobby", "maker", "gardening"]})
        create_program(get_principal(), {"title": "Volunteer-led guided art program", "module_keys": ["art"]})
    flash("Two independent working-title programs created with pending operating status and empty drafts.")
    return redirect(url_for("programs.index"))


@bp.get("/<program_id>")
@login_required
def workspace(program_id):
    from onpf.inquiries.clarifications import rounds
    program = get_program(get_principal(), program_id)
    is_owner = get_principal().user_id in program["owner_ids"]
    return render_template("programs/workspace.html", program=program, framework=load_framework(), readiness=readiness(program), is_owner=is_owner, clarification_rounds=rounds(get_principal(), program_id) if program["access_role"] in {"owner", "facilitator"} else [], can_edit=program["access_role"] in {"owner", "facilitator"})


@bp.post('/<program_id>/retirement')
@login_required
def retirement(program_id):
    from onpf.programs.retirement import retire,reactivate
    action=request.form.get('action')
    if action not in {'retire','reactivate'}:
        raise DomainError('invalid_action','Choose retire or reactivate privately.',422)
    (retire if action=='retire' else reactivate)(get_principal(),program_id,confirmed=request.form.get('confirmed')=='yes')
    flash('Project retired and public access withdrawn.' if action=='retire' else 'Project reactivated privately. A new public version requires explicit publication.')
    return redirect(url_for('programs.workspace',program_id=program_id))


@bp.route("/<program_id>/settings", methods=["GET", "POST"])
@login_required
def settings(program_id):
    program = get_program(get_principal(), program_id)
    if get_principal().user_id not in program["owner_ids"]:
        raise DomainError("forbidden", "Only a decision owner can change program settings.", 403)
    values, error, revision = program, None, program["revision"]
    member_roles = {member["user_id"]: member["role"] for member in program["memberships"]}
    if request.method == "POST":
        values = _setup_payload()
        member_roles = {member["user_id"]: member["role"] for member in values.get("memberships", [])}
        revision = request.form.get("expected_revision", "")
        try:
            update_program(get_principal(), program_id, values, _revision())
            flash("Program settings saved.")
            own_role = member_roles.get(get_principal().user_id)
            if 'memberships' in values and own_role not in {'owner', 'facilitator'}:
                return redirect(url_for('programs.index'))
            return redirect(url_for("programs.workspace", program_id=program_id))
        except DomainError as problem:
            error = problem
    return render_template("programs/create.html", values=values, program=program, framework=load_framework(), error=error, editing=True, expected_revision=revision, accounts=_accounts(), member_roles=member_roles), error.status if error else 200


@bp.route("/<program_id>/documents/<document_key>", methods=["GET", "POST"])
@login_required
def document(program_id, document_key):
    from onpf.refinement.service import list_decisions
    program, framework = get_program(get_principal(), program_id), load_framework()
    can_edit = program["access_role"] in {"owner", "facilitator"}
    if request.method == "POST" and not can_edit:
        raise DomainError("forbidden", "View-only access cannot change documents.", 403)
    structure = framework["documents"].get(document_key)
    if structure is None:
        raise DomainError("not_found", "This document is unavailable.", 404)
    content, error, revision = program["documents"][document_key], None, program["revision"]
    decision_ids = program['document_decision_ids'].get(document_key, [])
    if request.method == "POST":
        content = {"sections": {section["key"]: request.form.get(f'section_{section["key"]}', "") for section in structure["sections"]}}
        decision_ids = request.form.getlist('decision_ids')
        content['decision_ids'] = decision_ids
        revision = request.form.get("expected_revision", "")
        if document_key == "budget":
            # Retain every existing row plus the clearly labeled blank additions.
            indexes = sorted({key[5:] for key in request.form if key.startswith("item_")}, key=lambda value: (len(value), value))
            rows = []
            for index in indexes:
                row = {key: request.form.get(f'{key}_{index}', "") for key in ("item", "quantity", "unit_cost", "cost_status", "notes")}
                if any(row[key].strip() for key in ("item", "quantity", "unit_cost", "notes")):
                    rows.append(row)
            content["rows"] = rows
        try:
            save_document(get_principal(), program_id, document_key, content, _revision(), ai_receipt=request.form.get('ai_receipt') or None)
            flash("Draft saved.")
            return redirect(url_for("programs.document", program_id=program_id, document_key=document_key))
        except DomainError as problem:
            error = problem
    budget_rows = list(content.get("rows", [])) + [{"item": "", "quantity": "", "unit_cost": None, "cost_status": "estimated", "notes": ""} for _ in range(3)]
    return render_template("programs/document.html", program=program, structure=structure, content=content, error=error, expected_revision=revision, budget_rows=budget_rows, decisions=list_decisions(get_principal(), program_id) if can_edit else [], decision_ids=decision_ids, can_edit=can_edit, ai_receipt=request.form.get('ai_receipt') or None), error.status if error else 200

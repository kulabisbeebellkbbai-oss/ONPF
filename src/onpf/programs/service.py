"""Program authority and optimistic, transactional draft editing."""
import json
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from onpf.auth.models import Principal
from onpf.auth.service import require_project_creation, require_role
from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.framework import load_framework

EDIT_ROLES = {"owner", "facilitator"}
READ_ROLES = EDIT_ROLES | {"viewer"}


def _account(actor: Principal) -> None:
    if not isinstance(actor, Principal) or not actor.user_id or actor.invite_id or actor.batch_id or not get_db().execute("SELECT 1 FROM users WHERE id=?", (actor.user_id,)).fetchone():
        raise DomainError("forbidden", "An authenticated account is required.", 403)


def _text(value, label: str, maximum: int = 50000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise DomainError("invalid_text", f"{label} must be text of at most {maximum} characters.", 422)
    return value


def _settings(payload: Record, current: Record | None = None) -> Record:
    if not isinstance(payload, dict):
        raise DomainError("invalid_program", "Program details must be a record.", 422)
    values = dict(current or {"purpose": "", "local_context": "", "approval_rule": "all", "operating_status": "pending", "module_keys": []})
    for key in ("title", "purpose", "local_context", "approval_rule", "operating_status", "module_keys"):
        if key in payload:
            values[key] = payload[key]
    values["title"] = _text(values.get("title", ""), "Title", 200).strip()
    if not values["title"]:
        raise DomainError("invalid_title", "Enter a program title.", 422)
    for key in ("purpose", "local_context"):
        _text(values[key], key.replace("_", " ").title())
    if values["approval_rule"] not in ("all", "any"):
        raise DomainError("invalid_rule", "Choose all owners or any one owner.", 422)
    if values["operating_status"] not in ("pending", "confirmed", "not_applicable"):
        raise DomainError("invalid_status", "Choose pending, confirmed, or not applicable for permission to operate.", 422)
    keys = values["module_keys"]
    if not isinstance(keys, list) or any(not isinstance(key, str) or key not in load_framework()["modules"] or key == "core" for key in keys):
        raise DomainError("invalid_modules", "Choose available optional activity modules.", 422)
    values["module_keys"] = list(dict.fromkeys(keys))
    return values


def _members(payload: Record, actor: Principal, current: Record | None = None) -> dict[str, str]:
    roles = {m["user_id"]: m["role"] for m in (current or {}).get("memberships", [])}
    if current is None:
        roles[actor.user_id] = "owner"
    if "memberships" in payload:
        incoming = payload["memberships"]
        if not isinstance(incoming, list):
            raise DomainError("invalid_members", "Choose account memberships from the list.", 422)
        roles = {}
        for member in incoming:
            if not isinstance(member, dict) or not isinstance(member.get("user_id"), str) or member.get("role") not in ("owner", "facilitator", "contributor", "viewer") or member["user_id"] in roles:
                raise DomainError("invalid_members", "Each account needs one valid role.", 422)
            roles[member["user_id"]] = member["role"]
        # Creation always includes the creator unless an explicit owner list follows.
        if current is None:
            roles.setdefault(actor.user_id, "owner")
    if "owner_ids" in payload:
        owners = payload["owner_ids"]
        if not isinstance(owners, list) or not owners or any(not isinstance(owner, str) for owner in owners):
            raise DomainError("last_owner", "Keep at least one decision owner.", 422)
        roles = {uid: ("facilitator" if role == "owner" else role) for uid, role in roles.items()}
        roles.update({uid: "owner" for uid in owners})
    if "owner" not in roles.values():
        raise DomainError("last_owner", "Keep at least one decision owner.", 422)
    for user_id in roles:
        if not get_db().execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise DomainError("invalid_account", "One of the selected accounts is unavailable.", 422)
    return roles


def _snapshot(program_id: str) -> Record:
    row = get_db().execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
    if row is None:
        raise DomainError("not_found", "This program is unavailable.", 404)
    result = dict(row)
    result["module_keys"] = json.loads(result["module_keys"])
    result["memberships"] = [dict(member) for member in get_db().execute("SELECT m.user_id,m.role,u.username FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.program_id=? ORDER BY u.username", (program_id,))]
    result["owner_ids"] = [member["user_id"] for member in result["memberships"] if member["role"] == "owner"]
    result["documents"] = {row["document_key"]: json.loads(row["content"]) for row in get_db().execute("SELECT document_key,content FROM program_documents WHERE program_id=?", (program_id,))}
    result["document_decision_ids"] = {}
    for link in get_db().execute("SELECT document_key,decision_id FROM document_decisions WHERE program_id=? ORDER BY ordinal", (program_id,)):
        result["document_decision_ids"].setdefault(link["document_key"], []).append(link["decision_id"])
    for key, structure in load_framework()["documents"].items():
        result["documents"].setdefault(key, {"sections": {section["key"]: "" for section in structure["sections"]}, **({"rows": []} if key == "budget" else {})})
    return result


def get_program(actor: Principal, program_id: str) -> Record:
    require_role(actor, program_id, READ_ROLES)
    result = _snapshot(program_id)
    role = next(member["role"] for member in result["memberships"] if member["user_id"] == actor.user_id)
    result["access_role"] = role
    if role == "viewer":
        result["memberships"] = []
        result["owner_ids"] = []
        result["document_decision_ids"] = {}
    return result


def list_programs(actor: Principal) -> list[Record]:
    _account(actor)
    return [dict(row) for row in get_db().execute("SELECT p.id,p.title,p.operating_status,p.revision,m.role FROM programs p JOIN memberships m ON m.program_id=p.id WHERE m.user_id=? AND m.role IN ('owner','facilitator','viewer') ORDER BY p.title", (actor.user_id,))]


def create_program(actor: Principal, payload: Record) -> Record:
    _account(actor)
    require_project_creation(actor)
    with transaction() as connection:
        values = _settings(payload)
        roles = _members(payload, actor)
        program_id, now = str(uuid4()), utcnow()
        connection.execute("INSERT INTO programs(id,title,purpose,local_context,operating_status,approval_rule,module_keys,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (program_id, values["title"], values["purpose"], values["local_context"], values["operating_status"], values["approval_rule"], json.dumps(values["module_keys"]), now, now))
        connection.executemany("INSERT INTO memberships(program_id,user_id,role) VALUES (?,?,?)", [(program_id, uid, role) for uid, role in roles.items()])
        for key, structure in load_framework()["documents"].items():
            content = {"sections": {section["key"]: "" for section in structure["sections"]}}
            if key == "budget":
                content["rows"] = []
            connection.execute("INSERT INTO program_documents(program_id,document_key,content,updated_at) VALUES (?,?,?,?)", (program_id, key, json.dumps(content), now))
        return _snapshot(program_id)


def _revision(program: Record, expected_revision: int) -> None:
    if type(expected_revision) is not int or program["revision"] != expected_revision:
        raise DomainError("stale_revision", "The program changed since this page opened. Your text is preserved below. Open the current draft in another tab to reconcile it before saving.", 409)


def update_program(actor: Principal, program_id: str, payload: Record, expected_revision: int) -> Record:
    from onpf.programs.retirement import ensure_editable
    ensure_editable(program_id)
    with transaction() as connection:
        require_role(actor, program_id, {"owner"})
        current = _snapshot(program_id)
        _revision(current, expected_revision)
        values, roles = _settings(payload, current), _members(payload, actor, current)
        connection.execute("UPDATE programs SET title=?,purpose=?,local_context=?,operating_status=?,approval_rule=?,module_keys=?,revision=revision+1,updated_at=? WHERE id=?", (values["title"], values["purpose"], values["local_context"], values["operating_status"], values["approval_rule"], json.dumps(values["module_keys"]), utcnow(), program_id))
        connection.execute("DELETE FROM memberships WHERE program_id=?", (program_id,))
        connection.executemany("INSERT INTO memberships(program_id,user_id,role) VALUES (?,?,?)", [(program_id, uid, role) for uid, role in roles.items()])
        return _snapshot(program_id)


def _document_content(document_key: str, content: Record) -> Record:
    structure = load_framework()["documents"].get(document_key)
    if structure is None:
        raise DomainError("not_found", "This document structure is unavailable.", 404)
    if not isinstance(content, dict) or not isinstance(content.get("sections", {}), dict):
        raise DomainError("invalid_document", "Document sections must be a record of text fields.", 422)
    sections = content.get("sections", {})
    allowed = {section["key"] for section in structure["sections"]}
    if set(sections) - allowed or set(content) - {"sections", "rows"} or (document_key != "budget" and "rows" in content):
        raise DomainError("invalid_section", "Use the sections in this document structure.", 422)
    normalized = {"sections": {key: _text(sections.get(key, ""), "Section") for key in allowed}}
    if document_key == "budget":
        rows = content.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 500:
            raise DomainError("invalid_budget", "Use at most 500 budget rows.", 422)
        normalized["rows"] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("cost_status", "estimated") not in ("estimated", "confirmed"):
                raise DomainError("invalid_cost_status", "Label each cost estimated or confirmed.", 422)
            cost = row.get("unit_cost")
            if cost not in (None, ""):
                try:
                    decimal = Decimal(str(cost))
                    if not decimal.is_finite() or decimal < 0:
                        raise InvalidOperation
                    cost = str(decimal)
                except (InvalidOperation, ValueError):
                    raise DomainError("invalid_cost", "Enter a nonnegative cost or leave it blank when unknown.", 422)
            else:
                cost = None
            normalized["rows"].append({"item": _text(row.get("item", ""), "Item", 1000), "quantity": _text(row.get("quantity", ""), "Quantity", 100), "unit_cost": cost, "cost_status": row.get("cost_status", "estimated"), "notes": _text(row.get("notes", ""), "Notes", 5000)})
    return normalized


def save_document(actor: Principal, program_id: str, document_key: str, content: Record, expected_revision: int, *, ai_receipt: str | None = None) -> Record:
    from onpf.programs.retirement import ensure_editable
    ensure_editable(program_id)
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        program = _snapshot(program_id)
        _revision(program, expected_revision)
        decision_ids = None
        if isinstance(content, dict) and "decision_ids" in content:
            from onpf.refinement.service import validate_document_decisions
            decision_ids = validate_document_decisions(actor, program_id, content["decision_ids"])
            content = {key: value for key, value in content.items() if key != "decision_ids"}
        normalized, now = _document_content(document_key, content), utcnow()
        from onpf.drafting import provenance
        verified = provenance._prepare_save(actor, program_id, 'document', ai_receipt, document_key=document_key)
        connection.execute("INSERT INTO program_documents(program_id,document_key,content,updated_at) VALUES (?,?,?,?) ON CONFLICT(program_id,document_key) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at", (program_id, document_key, json.dumps(normalized, ensure_ascii=False), now))
        if decision_ids is not None:
            connection.execute("DELETE FROM document_decisions WHERE program_id=? AND document_key=?", (program_id, document_key))
            connection.executemany("INSERT INTO document_decisions(program_id,document_key,decision_id,ordinal) VALUES (?,?,?,?)", [(program_id, document_key, decision_id, index) for index, decision_id in enumerate(decision_ids)])
        connection.execute("UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?", (now, program_id))
        if verified:
            saved = _snapshot(program_id)
            provenance._attach_verified(actor, program_id, {'kind': 'document', 'document_key': document_key}, verified,
                                        {**normalized, 'decision_ids': saved['document_decision_ids'].get(document_key, [])})
        return _snapshot(program_id)


def readiness(program: Record) -> Record:
    from onpf.permissions.service import permission_readiness
    framework = load_framework()
    stage_status = []
    for stage in framework["stages"]:
        missing = []
        for key in stage["document_keys"]:
            for section in framework["documents"][key]["sections"]:
                if not program["documents"].get(key, {}).get("sections", {}).get(section["key"], "").strip():
                    missing.append(f'{framework["documents"][key]["title"]}: {section["label"]}')
        stage_status.append({**stage, "missing": missing})
    checks = [check for key in program["module_keys"] for check in framework["modules"][key]["readiness_checks"]]
    return {"stages": stage_status, "module_checks": [{**check, "drafted": bool(program["documents"].get(check["document_key"], {}).get("sections", {}).get(check["section_key"], "").strip())} for check in checks], "operating_permission": permission_readiness(program["id"])}

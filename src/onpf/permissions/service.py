"""Private, versioned evidence for site and operating permissions."""
from datetime import date
from base64 import b64decode, b64encode
from uuid import uuid4

from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.programs.service import EDIT_ROLES

COPY_DEFAULTS = (
    ("site_owner", "Site owner or issuer"),
    ("coordinating_group", "Coordinating group"),
    ("official_record", "Official program record"),
    ("site_posting", "Site posting"),
)
MAX_EVIDENCE = 5 * 1024 * 1024
EVIDENCE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".odt", ".docx", ".txt"}


def _text(value, label, limit=5000, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        raise DomainError("invalid_permission", f"Enter {label} as text of at most {limit} characters.", 422)
    return value.strip()


def _date(value, label, required=False):
    if value in (None, "") and not required:
        return None
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise DomainError("invalid_date", f"Enter a valid {label} date.", 422) from None
    return value


def _copies(values):
    if not isinstance(values, list) or len(values) > len(COPY_DEFAULTS):
        raise DomainError("invalid_copies", "Choose the four permission copy states.", 422)
    by_key = {}
    for row in values:
        if not isinstance(row, dict) or row.get("key") not in dict(COPY_DEFAULTS) or row["key"] in by_key:
            raise DomainError("invalid_copies", "Choose each copy recipient once.", 422)
        key = row["key"]
        state = row.get("state", "unknown")
        if state not in ("unknown", "delivered", "posted", "not_required") or (key == "site_posting" and state == "delivered") or (key != "site_posting" and state == "posted"):
            raise DomainError("invalid_copies", "Choose a valid copy state for each recipient.", 422)
        recorded_on = _date(row.get("date"), "copy")
        if state in ("delivered", "posted") and not recorded_on or state in ("unknown", "not_required") and recorded_on:
            raise DomainError("invalid_copies", "Dated copy states need a date; unknown and not required stay undated.", 422)
        by_key[key] = {"key": key, "label": _text(row.get("label", ""), "a recipient label", 200, True), "state": state, "date": recorded_on}
    return [by_key.get(key, {"key": key, "label": label, "state": "unknown", "date": None}) for key, label in COPY_DEFAULTS]


def _record(record_id):
    row = get_db().execute("SELECT * FROM permission_records WHERE id=?", (record_id,)).fetchone()
    if row is None:
        raise DomainError("not_found", "This permission record is unavailable.", 404)
    return dict(row)


def _copy_rows(record_id):
    return [{"key": row["recipient_key"], "label": row["recipient_label"], "state": row["state"], "date": row["recorded_on"]} for row in get_db().execute("SELECT * FROM permission_copies WHERE permission_id=? ORDER BY rowid", (record_id,))]


def _snapshot(row, *, include_evidence=False):
    result = {key: value for key, value in row.items() if key != "evidence"}
    result["copies"] = _copy_rows(row["id"])
    revoked = get_db().execute("SELECT revoked_on,reason,recorded_by,recorded_at FROM permission_revocations WHERE permission_id=?", (row["id"],)).fetchone()
    result["revocation"] = dict(revoked) if revoked else None
    if include_evidence:
        result["evidence"] = b64decode(row["evidence"]) if row["evidence"] else None
    return result


def save_permission(actor, program_id: str, payload: dict, evidence: bytes | None):
    with transaction() as connection:
        require_role(actor, program_id, EDIT_ROLES)
        if not isinstance(payload, dict):
            raise DomainError("invalid_permission", "Enter the permission fields.", 422)
        issuer = _text(payload.get("issuer", ""), "an issuer", 200, True)
        effective_on = _date(payload.get("effective_on"), "effective", True)
        expires_on = _date(payload.get("expires_on"), "expiry")
        if expires_on and expires_on < effective_on:
            raise DomainError("invalid_date", "Expiry cannot precede the effective date.", 422)
        scope = _text(payload.get("scope", ""), "a scope", 5000, True)
        conditions = _text(payload.get("conditions", ""), "conditions", 10000)
        filed_on = _date(payload.get("filed_on"), "filing")
        if evidence is not None and (not isinstance(evidence, bytes) or not evidence or len(evidence) > MAX_EVIDENCE):
            raise DomainError("invalid_evidence", "Attach a nonempty permission file of at most 5 MB.", 422)
        if filed_on and not evidence:
            raise DomainError("unfiled_permission", "Attach the issued permission before marking it filed.", 422)
        name = _text(payload.get("evidence_name", ""), "an evidence filename", 255)
        name = name.replace("\\", "/").split("/")[-1]
        if evidence and (not name or "." + name.rsplit(".", 1)[-1].lower() not in EVIDENCE_EXTENSIONS):
            raise DomainError("invalid_evidence", "Attach PDF, PNG, JPEG, ODT, DOCX, or TXT evidence.", 422)
        copies = _copies(payload.get("copies", []))
        latest = connection.execute("SELECT id,version FROM permission_records WHERE program_id=? ORDER BY version DESC LIMIT 1", (program_id,)).fetchone()
        record_id, now = str(uuid4()), utcnow()
        connection.execute("INSERT INTO permission_records(id,program_id,version,issuer,effective_on,expires_on,scope,conditions,filed_on,evidence_name,evidence,replaces_id,recorded_by,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record_id, program_id, latest["version"] + 1 if latest else 1, issuer, effective_on, expires_on, scope, conditions, filed_on, name if evidence else None, b64encode(evidence).decode('ascii') if evidence else None, latest["id"] if latest else None, actor.user_id, now))
        connection.executemany("INSERT INTO permission_copies(permission_id,recipient_key,recipient_label,state,recorded_on) VALUES (?,?,?,?,?)", [(record_id, row["key"], row["label"], row["state"], row["date"]) for row in copies])
        connection.execute("UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?", (now, program_id))
        return _snapshot(_record(record_id))


def get_permission(actor, record_id: str):
    row = _record(record_id)
    require_role(actor, row["program_id"], EDIT_ROLES)
    return _snapshot(row, include_evidence=True)


def list_permissions(actor, program_id: str):
    require_role(actor, program_id, EDIT_ROLES)
    return [_snapshot(dict(row)) for row in get_db().execute("SELECT * FROM permission_records WHERE program_id=? ORDER BY version", (program_id,))]


def revoke_permission(actor, record_id: str, revoked_on: str, reason: str):
    with transaction() as connection:
        row = _record(record_id)
        require_role(actor, row["program_id"], {"owner"})
        when = _date(revoked_on, "revocation", True)
        explanation = _text(reason, "a revocation reason", 2000, True)
        if connection.execute("SELECT 1 FROM permission_revocations WHERE permission_id=?", (record_id,)).fetchone():
            raise DomainError("already_revoked", "This permission was already revoked.", 409)
        now = utcnow()
        connection.execute("INSERT INTO permission_revocations(permission_id,revoked_on,reason,recorded_by,recorded_at) VALUES (?,?,?,?,?)", (record_id, when, explanation, actor.user_id, now))
        connection.execute("UPDATE programs SET revision=revision+1,updated_at=? WHERE id=?", (now, row["program_id"]))
        return _snapshot(_record(record_id))


def permission_readiness(program_id: str):
    row = get_db().execute("SELECT * FROM permission_records WHERE program_id=? ORDER BY version DESC LIMIT 1", (program_id,)).fetchone()
    if row is None:
        return {"status": "missing", "issues": ["No operating-permission evidence has been filed."], "record": None}
    record = _snapshot(dict(row))
    today = date.today().isoformat()
    if record["revocation"]:
        status, issues = "revoked", ["The latest permission has been revoked; review the replacement before use."]
    elif record["expires_on"] and record["expires_on"] < today:
        status, issues = "expired", ["The latest permission has expired."]
    elif record["effective_on"] > today:
        status, issues = "not_effective", ["The latest permission is not yet effective."]
    elif not record["filed_on"] or not row["evidence"]:
        status, issues = "unfiled", ["The latest permission has not been evidenced and filed."]
    else:
        missing = [copy["label"] for copy in record["copies"] if copy["state"] == "unknown"]
        status = "incomplete_copies" if missing else "current"
        issues = ["Copy status is unknown for: " + ", ".join(missing)] if missing else []
    return {"status": status, "issues": issues, "record": record}


def review_required(program: dict) -> bool:
    """Acknowledge unresolved evidence before approving an operating-site design."""
    status = permission_readiness(program["id"])["status"]
    return ("gardening" in program["module_keys"] or program["operating_status"] == "confirmed") and status != "current"

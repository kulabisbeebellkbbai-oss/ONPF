"""Organizer context is revisioned documentation, never permission or a decision."""
import json
from uuid import uuid4
from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError


def history(actor, program_id, clarification_id):
    require_role(actor, program_id, {'owner','facilitator','viewer'})
    return [{**dict(row), 'context': json.loads(row['context_json'])} for row in get_db().execute(
        'SELECT * FROM organizer_clarifications WHERE program_id=? AND id=? ORDER BY revision',
        (program_id, clarification_id))]


def current(actor, program_id):
    require_role(actor, program_id, {'owner','facilitator','viewer'})
    return [{**dict(row), 'context':json.loads(row['context_json'])} for row in get_db().execute(
        'SELECT c.* FROM organizer_clarifications c WHERE program_id=? AND revision=(SELECT MAX(revision) FROM organizer_clarifications r WHERE r.id=c.id) ORDER BY saved_at,id', (program_id,))]


def save(actor, program_id, text, context, clarification_id=None, expected_revision=None):
    from onpf.drafting.evidence import validate_target
    context = validate_target(context)
    if not isinstance(text,str) or not text.strip() or len(text)>4000:
        raise DomainError('invalid_clarification','Enter a clarifying statement of at most 4000 characters.',422)
    with transaction() as db:
        require_role(actor, program_id, {'owner','facilitator'})
        records = history(actor, program_id, clarification_id) if clarification_id else []
        if clarification_id and (not records or records[-1]['revision'] != expected_revision):
            raise DomainError('stale_revision','This statement changed. Review its history before correcting it.',409)
        key = clarification_id or str(uuid4())
        revision = records[-1]['revision']+1 if records else 1
        db.execute('INSERT INTO organizer_clarifications VALUES (?,?,?,?,?,?,?)',
                   (key,program_id,revision,text,json.dumps(context),actor.user_id,utcnow()))
        return history(actor, program_id, key)[-1]

"""Withdraw public access without changing private or frozen document content."""
from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError


def ensure_editable(program_id):
    row=get_db().execute('SELECT retired_at FROM programs WHERE id=?',(program_id,)).fetchone()
    if row and row['retired_at']:
        raise DomainError('project_retired','This project is retired. Authorized history remains available; reactivate privately before editing.',409)


def retire(actor,program_id,*,confirmed=False):
    _change(actor,program_id,True,confirmed)


def reactivate(actor,program_id,*,confirmed=False):
    _change(actor,program_id,False,confirmed)


def _change(actor,program_id,retired,confirmed):
    with transaction() as db:
        require_role(actor,program_id,{'owner'})
        if confirmed is not True:
            raise DomainError('confirmation_required','Confirm withdrawal of all public pages and downloads, or private reactivation.',422)
        row=db.execute('SELECT retired_at FROM programs WHERE id=?',(program_id,)).fetchone()
        if bool(row['retired_at'])==retired:
            return
        now=utcnow()
        cutoff=db.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM publications WHERE program_id=?',(program_id,)).fetchone()[0]
        db.execute('UPDATE programs SET retired_at=?,retired_by=?,public_from_version=?,revision=revision+1,updated_at=? WHERE id=?',
                   (now if retired else None,actor.user_id if retired else None,cutoff,now,program_id))
        # Invitations remain in private history but are no longer usable.
        if retired:
            db.execute('UPDATE invitations SET revoked_at=? WHERE batch_id IN (SELECT id FROM batches WHERE program_id=?) AND revoked_at IS NULL',(now,program_id))
            db.execute('UPDATE review_drafts SET revoked_at=? WHERE program_id=? AND revoked_at IS NULL',(now,program_id))
        db.execute('INSERT INTO project_retirement_events(program_id,actor_id,action,happened_at) VALUES (?,?,?,?)',
                   (program_id,actor.user_id,'retire' if retired else 'reactivate_private',now))

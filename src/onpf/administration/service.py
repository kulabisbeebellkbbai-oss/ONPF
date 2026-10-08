"""Administrative access changes and effective drafting policy."""
import json
from flask import current_app
from onpf.auth.service import require_admin, require_role
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError


def audit(actor, action, scope, target, changes):
    get_db().execute('INSERT INTO administrative_events(actor_id,happened_at,action,scope,target_id,changes_json) VALUES (?,?,?,?,?,?)',
                     (actor.user_id,utcnow(),action,scope,target,json.dumps(changes)))


def authorize_scope(actor, scope, target):
    if scope in {'system','user'}:
        require_admin(actor)
    elif scope == 'project':
        try:
            require_admin(actor)
        except DomainError:
            require_role(actor,target,{'owner'})
    else:
        raise DomainError('invalid_scope','Choose system, project or user.',422)
    if scope == 'system' and target != '':
        raise DomainError('invalid_scope','System controls apply to the entire installation.',422)
    table = {'user':'users','project':'programs'}.get(scope)
    if table and not get_db().execute(f'SELECT 1 FROM {table} WHERE id=?',(target,)).fetchone():
        raise DomainError('not_found','That administrative target is unavailable.',404)


def set_access(actor, user_id, active):
    with transaction() as db:
        require_admin(actor)
        if type(active) is not bool:
            raise DomainError('invalid_access','Choose active or removed access.',422)
        row = db.execute('SELECT active,is_admin FROM users WHERE id=?',(user_id,)).fetchone()
        if not row:
            raise DomainError('not_found','That account is unavailable.',404)
        if not active and row['is_admin'] and not db.execute('SELECT 1 FROM users WHERE is_admin=1 AND active=1 AND id!=?',(user_id,)).fetchone():
            raise DomainError('last_admin','Keep at least one active system administrator.',422)
        db.execute('UPDATE users SET active=? WHERE id=?',(int(active),user_id))
        if not active:
            db.execute('DELETE FROM auth_sessions WHERE user_id=?',(user_id,))
        audit(actor,'access','user',user_id,{'old':bool(row['active']),'new':active})


def set_ai(actor, scope, target, enabled):
    with transaction() as db:
        authorize_scope(actor,scope,target)
        if type(enabled) is not bool:
            raise DomainError('invalid_policy','Choose on or off.',422)
        old = db.execute('SELECT enabled FROM ai_policies WHERE scope=? AND target_id=?',(scope,target)).fetchone()
        db.execute('INSERT INTO ai_policies(scope,target_id,enabled) VALUES (?,?,?) ON CONFLICT(scope,target_id) DO UPDATE SET enabled=excluded.enabled',
                   (scope,target,int(enabled)))
        audit(actor,'ai_switch',scope,target,{'old':bool(old['enabled']) if old else True,'new':enabled})


def effective(actor, program_id):
    require_role(actor,program_id,{'owner','facilitator'})
    scopes = [('system',''),('project',program_id),('user',actor.user_id)]
    for scope,key in scopes:
        row = get_db().execute('SELECT enabled FROM ai_policies WHERE scope=? AND target_id=?',(scope,key)).fetchone()
        if row and not row['enabled']:
            return {'enabled':False,'scope':scope,'reason':f'AI drafting is off at {scope} scope.'}
    if not current_app.config['AI_DRAFTING_ENABLED']:
        return {'enabled':False,'scope':'installation','reason':'AI drafting is disabled for this installation.'}
    return {'enabled':True,'scope':'all','reason':'System, project and user settings permit AI drafting.'}


def require_ai(actor, program_id):
    state = effective(actor,program_id)
    if not state['enabled']:
        raise DomainError('ai_disabled' if state['scope']=='installation' else 'ai_policy_disabled',state['reason'],503)
    return state

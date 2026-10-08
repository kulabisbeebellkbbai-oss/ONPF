from flask import Blueprint, current_app, redirect, render_template, request, url_for
from onpf.auth.service import (authenticate, create_session, create_user, get_principal,
    management_state, require_admin, set_membership, set_project_creation, revoke_session)
from onpf.administration import service
from onpf.db import get_db, transaction
from onpf.errors import DomainError

bp = Blueprint('administration',__name__)


@bp.after_request
def private(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


@bp.route('/login',methods=['GET','POST'])
def login():
    error=None
    if request.method=='POST':
        try:
            actor=authenticate(request.form.get('username',''),request.form.get('password',''))
            management_state(actor)
            token=create_session(actor)
            response=redirect(url_for('administration.index'))
            response.set_cookie(current_app.config['AUTH_COOKIE_NAME'],token,httponly=True,samesite='Lax',secure=current_app.config.get('AUTH_COOKIE_SECURE',False))
            return response
        except DomainError as problem:
            error=problem
    return render_template('administration/login.html',error=error), error.status if error else 200


@bp.route('/',methods=['GET','POST'])
def index():
    actor=get_principal()
    if not actor.user_id:
        return redirect(url_for('administration.login'))
    state=management_state(actor)
    error=None
    if request.method=='POST':
        try:
            with transaction():
                action=request.form.get('action')
                uid=request.form.get('user_id','')
                if action=='access':
                    service.set_access(actor,uid,request.form.get('active')=='yes')
                elif action=='ai':
                    service.set_ai(actor,request.form.get('scope',''),request.form.get('target_id',''),request.form.get('enabled')=='yes')
                elif action=='membership':
                    old=get_db().execute('SELECT role FROM memberships WHERE program_id=? AND user_id=?',(request.form.get('program_id',''),uid)).fetchone()
                    set_membership(actor,request.form.get('program_id',''),uid,request.form.get('role',''))
                    service.audit(actor,action,'project',request.form.get('program_id',''),{'user_id':uid,'old':old['role'] if old else '', 'new':request.form.get('role','')})
                elif action=='creation':
                    old=get_db().execute('SELECT can_create_projects FROM users WHERE id=?',(uid,)).fetchone()
                    set_project_creation(actor,uid,request.form.get('allowed')=='yes')
                    service.audit(actor,action,'user',uid,{'old':bool(old['can_create_projects']) if old else None,'new':request.form.get('allowed')=='yes'})
                elif action=='create_user':
                    require_admin(actor)
                    uid=create_user(request.form.get('username',''),request.form.get('password',''))
                    service.audit(actor,action,'user',uid,{'created':True})
                elif action=='limits':
                    from onpf.drafting.usage import set_limits
                    set_limits(actor,request.form.get('scope',''),request.form.get('target_id',''),request.form)
                else:
                    raise DomainError('invalid_action','Choose an administration action.',422)
            return redirect(url_for('administration.index'))
        except DomainError as problem:
            error=problem
    state=management_state(actor)
    active={r['id']:bool(r['active']) for r in get_db().execute('SELECT id,active FROM users')}
    policies={(r['scope'],r['target_id']):dict(r) for r in get_db().execute('SELECT * FROM ai_policies')}
    events=[dict(r) for r in get_db().execute('SELECT e.*,u.username FROM administrative_events e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT 100')] if state['is_admin'] else []
    from onpf.drafting.usage import DEFAULT_LIMITS
    from onpf.administration.service import effective
    availability={program['id']:effective(actor,program['id']) for program in state['programs']
                  if get_db().execute("SELECT 1 FROM memberships WHERE program_id=? AND user_id=? AND role IN ('owner','facilitator')",(program['id'],actor.user_id)).fetchone()}
    parsed={(scope,key):__import__('json').loads(value['limits_json']) for (scope,key),value in policies.items()}
    charges=[dict(r) for r in get_db().execute('SELECT a.started_at,a.state,a.reserved_micro,a.charged_micro,u.username,p.title FROM ai_usage a JOIN users u ON u.id=a.user_id JOIN programs p ON p.id=a.program_id ORDER BY a.started_at DESC LIMIT 100')] if state['is_admin'] else []
    return render_template('administration/index.html',state=state,active=active,policies=policies,events=events,error=error,
                           parsed_limits=parsed,default_limits=DEFAULT_LIMITS,availability=availability,charges=charges),error.status if error else 200

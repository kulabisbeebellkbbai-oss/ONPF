"""Atomic multi-scope reservations. Accounting contains no authored content."""
import json
import math
from uuid import uuid4
from flask import current_app
from onpf.administration.service import authorize_scope, audit, require_ai
from onpf.db import get_db, transaction, utcnow
from onpf.errors import DomainError

DEFAULT_LIMITS = {'daily_requests':60, 'concurrency':1, 'timeout_seconds':45,
    'evidence_bytes':96000, 'request_bytes':131072, 'output_tokens':4096,
    'output_bytes':65536, 'batch_items':20, 'pages':20, 'storage_bytes':52428800}
MAXIMUMS = {**DEFAULT_LIMITS, 'daily_requests':10000, 'batch_items':100,
    'pages':100, 'storage_bytes':1073741824, 'daily_budget_micro':1000000000,
    'input_micro_per_1000':1000000000,'output_micro_per_1000':1000000000}


def set_limits(actor,scope,target,values):
    with transaction() as db:
        authorize_scope(actor,scope,target)
        row=db.execute('SELECT limits_json FROM ai_policies WHERE scope=? AND target_id=?',(scope,target)).fetchone()
        limits=json.loads(row['limits_json']) if row else {}
        old=dict(limits)
        for key,maximum in MAXIMUMS.items():
            if key not in values:
                continue
            value=values[key]
            if value in ('',None):
                limits.pop(key,None)
                continue
            minimum=0 if key in {'input_micro_per_1000','output_micro_per_1000','daily_budget_micro'} else 1
            if isinstance(value,bool) or not str(value).isdigit() or not minimum<=int(value)<=maximum:
                raise DomainError('invalid_limit',f'Choose {key.replace("_"," ")} from {minimum} to {maximum}.',422)
            if key in {'input_micro_per_1000','output_micro_per_1000'} and scope!='system':
                raise DomainError('invalid_scope','Only system administration can configure provider prices.',422)
            limits[key]=int(value)
        if 'priced_model' in values:
            if scope!='system' or not isinstance(values['priced_model'],str) or len(values['priced_model'])>100:
                raise DomainError('invalid_price','Set the priced model in system administration.',422)
            limits['priced_model']=values['priced_model']
        db.execute('INSERT INTO ai_policies(scope,target_id,enabled,limits_json) VALUES (?,?,1,?) ON CONFLICT(scope,target_id) DO UPDATE SET limits_json=excluded.limits_json',(scope,target,json.dumps(limits)))
        audit(actor,'ai_limits',scope,target,{'old':old,'new':limits})


def policies(actor,program_id):
    result=[]
    for scope,key in [('system',''),('project',program_id),('user',actor.user_id)]:
        row=get_db().execute('SELECT limits_json FROM ai_policies WHERE scope=? AND target_id=?',(scope,key)).fetchone()
        result.append((scope,key,json.loads(row['limits_json']) if row else {}))
    return result


def limits(actor,program_id):
    result=dict(DEFAULT_LIMITS)
    for scope,_,policy in policies(actor,program_id):
        for key in result:
            result[key]=policy.get(key,result[key]) if scope=='system' else min(result[key],policy.get(key,result[key]))
    return result


def estimate(actor,program_id,input_bound,output_bound):
    policy=policies(actor,program_id)[0][2]
    known=(policy.get('priced_model')==current_app.config['AI_MODEL'] and
           'input_micro_per_1000' in policy and 'output_micro_per_1000' in policy)
    if not known:
        return None
    return math.ceil(input_bound*policy['input_micro_per_1000']/1000)+math.ceil(output_bound*policy['output_micro_per_1000']/1000)


def reserve(actor,program_id,request_bytes,input_bound,output_bound):
    with transaction() as db:
        require_ai(actor,program_id)
        bound=limits(actor,program_id)
        if request_bytes>bound['request_bytes'] or output_bound>bound['output_tokens']:
            raise DomainError('ai_resource_limit','This request exceeds its configured size limits. Reduce the selected content.',422)
        maximum=estimate(actor,program_id,input_bound,output_bound)
        applicable=policies(actor,program_id)
        if maximum is None and any('daily_budget_micro' in policy for _,_,policy in applicable):
            raise DomainError('ai_pricing_unknown','Reliable pricing is unavailable. Paid drafting is blocked by the monetary cap; manual editing is available.',503)
        now=utcnow()
        for scope,key,policy in applicable:
            clauses=['day=?']; values=[now[:10]]
            if scope!='system':
                clauses.append(('program_id' if scope=='project' else 'user_id')+'=?')
                values.append(key)
            row=db.execute('SELECT COUNT(*) AS requests,COALESCE(SUM(charged_micro),0) AS cost,COALESCE(SUM(state="reserved"),0) AS active FROM ai_usage WHERE '+' AND '.join(clauses),values).fetchone()
            if row['requests']>=policy.get('daily_requests',DEFAULT_LIMITS['daily_requests']):
                raise DomainError('ai_request_limit',f'The {scope} daily request allowance is reached. Manual editing is available.',429)
            # Outstanding attempts count across dates; unresolved leases fail closed.
            active_where='state="reserved"'
            active_values=[]
            if scope!='system':
                active_where+=' AND '+('program_id' if scope=='project' else 'user_id')+'=?'
                active_values=[key]
            active=db.execute('SELECT COUNT(*) FROM ai_usage WHERE '+active_where,active_values).fetchone()[0]
            if active>=policy.get('concurrency',DEFAULT_LIMITS['concurrency']):
                raise DomainError('ai_concurrency_limit','Another reserved request is pending. Retry explicitly after it completes.',429)
            if 'daily_budget_micro' in policy and row['cost']+(maximum or 0)>policy['daily_budget_micro']:
                raise DomainError('ai_budget_exhausted',f'The {scope} daily spending allowance is reached. Manual editing is available.',429)
        key=str(uuid4()); price=applicable[0][2]
        db.execute('INSERT INTO ai_usage(id,user_id,program_id,started_at,day,state,reserved_micro,charged_micro,input_price,output_price) VALUES (?,?,?,?,?,"reserved",?,?,?,?)',
                   (key,actor.user_id,program_id,now,now[:10],maximum or 0,maximum or 0,price.get('input_micro_per_1000'),price.get('output_micro_per_1000')))
        return key


def finish(reservation,usage,*,dispatched=True):
    with transaction() as db:
        row=db.execute('SELECT * FROM ai_usage WHERE id=?',(reservation,)).fetchone()
        if not row or row['state']!='reserved':
            return
        reliable=(isinstance(usage,dict) and all(type(usage.get(k)) is int and 0<=usage[k]<=10000000 for k in ('prompt_tokens','completion_tokens')))
        cost=row['reserved_micro']
        state='uncertain'
        if not dispatched:
            cost=0; state='cancelled'
        elif reliable:
            state='complete'
            if row['input_price'] is not None and row['output_price'] is not None:
                cost=math.ceil(usage['prompt_tokens']*row['input_price']/1000)+math.ceil(usage['completion_tokens']*row['output_price']/1000)
                if cost>row['reserved_micro']:
                    state='price_discrepancy'
        db.execute('UPDATE ai_usage SET state=?,charged_micro=?,usage_json=?,finished_at=? WHERE id=?',
                   (state,cost,json.dumps(usage) if reliable else None,utcnow(),reservation))

"""Signed content-free fingerprints of the catalog the editor reviewed."""
from flask import current_app
from itsdangerous import BadData,URLSafeTimedSerializer
from onpf.drafting import evidence
from onpf.errors import DomainError


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='onpf-reviewed-source-selection-v1')


def _target(target):
    return {key:value for key,value in target.items() if key!='mode'}


def issue(actor,program_id,target,sources):
    return _serializer().dumps({'actor':actor.user_id,'program':program_id,'target':_target(target),
        'sources':{source['handle']:evidence._hash(evidence._manifest(source)) for source in sources}})


def load(actor,program_id,target,token):
    try:
        if not isinstance(token,str) or not token or len(token)>262144:
            raise BadData('missing state')
        payload=_serializer().loads(token,max_age=3600)
        if (not isinstance(payload,dict) or payload.get('actor')!=actor.user_id or
            payload.get('program')!=program_id or payload.get('target')!=_target(target) or
            not isinstance(payload.get('sources'),dict)):
            raise BadData('wrong scope')
        return payload['sources']
    except BadData:
        raise DomainError('stale_evidence','The evidence review expired or is unavailable. Review the current previews and explicitly refresh sources before generating. Your wording is retained.',409) from None


def verify(actor,program_id,target,token,handles,sources):
    reviewed=load(actor,program_id,target,token)
    evidence._handles(handles)
    current={source['handle']:evidence._hash(evidence._manifest(source)) for source in sources}
    if any(handle not in reviewed or current.get(handle)!=reviewed[handle] for handle in handles):
        raise DomainError('stale_evidence','Selected evidence changed or became unavailable. Read the current previews and explicitly refresh sources before generating. Your wording and selections are retained.',409)
    return {handle:reviewed[handle] for handle in handles}

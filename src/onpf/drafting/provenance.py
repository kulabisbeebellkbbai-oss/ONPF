"""Ephemeral signed receipts and append-only, content-free explicit-save origins.

Receipt freshness is strict before mutation. Display freshness compares accepted
save fingerprints, preserving original manifests unchanged as historical evidence.
"""
import json
from dataclasses import dataclass
from uuid import uuid4

from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer

from onpf.auth.models import Principal
from onpf.auth.service import require_role
from onpf.db import get_db, transaction, utcnow
from onpf.drafting import evidence
from onpf.drafting.config import DEFAULTS
from onpf.errors import DomainError

RECEIPT_SALT = 'onpf-ai-drafting-receipt-v1'
RECEIPT_LIFETIME = 3600


def _invalid():
    raise DomainError('invalid_ai_receipt', 'This drafting receipt is invalid or expired. Review current sources and generate again, or save your reviewed wording manually.', 409)


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=RECEIPT_SALT)


def _authorize(actor, program_id, target):
    require_role(actor, program_id, {'owner'} if target['kind'] == 'decision' else {'owner', 'facilitator'})


def _revision(program_id):
    return get_db().execute('SELECT revision FROM programs WHERE id=?', (program_id,)).fetchone()[0]


def _scoped(target):
    return {**target, 'record_key': target['document_key']} if target['kind'] == 'document' else target


def issue_receipt(actor: Principal, program_id: str, target: dict, manifests: list[dict], output: dict) -> str:
    """Sign hashes and server manifests only; issuance performs no writes."""
    target = evidence.validate_target(target)
    _authorize(actor, program_id, target)
    evidence.catalog(actor, program_id, _scoped(target))
    evidence.assert_current(actor, program_id, manifests, review=target['kind'] == 'review')
    if not isinstance(output, dict) or not isinstance(output.get('fields'), dict):
        _invalid()
    payload = {'version': 1, 'actor_id': actor.user_id, 'program_id': program_id,
               'target': target, 'program_revision': _revision(program_id),
               'sources': manifests, 'generated_hash': evidence._hash(output['fields']),
               'output_hash': evidence._hash(output),
               'model_alias': current_app.config.get('AI_MODEL', DEFAULTS['AI_MODEL']),
               'generated_at': utcnow()}
    return _serializer().dumps(payload)


def _load(receipt):
    if not isinstance(receipt, str) or not receipt or len(receipt) > 131072:
        _invalid()
    try:
        payload = _serializer().loads(receipt, max_age=RECEIPT_LIFETIME)
    except BadData:
        _invalid()
    if not isinstance(payload, dict) or payload.get('version') != 1:
        _invalid()
    return payload


def validate_receipt(actor: Principal, program_id: str, target: dict, receipt: str) -> dict:
    """Reauthorize and verify exact request binding and current evidence."""
    target = evidence.validate_target(target)
    _authorize(actor, program_id, target)
    payload = _load(receipt)
    if (payload['actor_id'] != actor.user_id or payload['program_id'] != program_id
            or evidence._encode(payload['target']) != evidence._encode(target)):
        _invalid()
    # Check fingerprints first so response corrections/removal are actionable.
    evidence.assert_current(actor, program_id, payload['sources'], review=target['kind'] == 'review')
    try:
        evidence.catalog(actor, program_id, _scoped(target))
    except DomainError as error:
        if error.code != 'invalid_target':
            raise
        raise DomainError('stale_evidence', 'The drafting target became unavailable. Review current sources before saving with an origin.', 409) from None
    if payload['program_revision'] != _revision(program_id):
        raise DomainError('stale_evidence', 'The program changed since generation. Review current sources and generate again, or save reviewed wording manually.', 409)
    return payload


@dataclass(frozen=True)
class _Verified:
    payload: dict
    connection: object


def _prepare_save(actor, program_id, kind, receipt, *, record_key=None, stage=None, document_key=None):
    """Bind the signed form target before IDs or domain changes are assigned."""
    if receipt is None:
        return None
    if not get_db().in_transaction:
        raise RuntimeError('Receipt validation requires the domain save transaction')
    target = _load(receipt)['target']
    expected_key = document_key if kind == 'document' and 'record_key' in target else record_key
    if kind == 'decision' and 'record_key' not in target:
        expected_key = None
    if (target.get('kind') != kind or target.get('record_key') != expected_key
            or kind == 'document' and target.get('document_key') != document_key
            or kind == 'questions' and ('stage' in target and target['stage'] != stage
                                       or 'document_key' in target and target['document_key'] != document_key)):
        _invalid()
    return _Verified(validate_receipt(actor, program_id, target, receipt), get_db())


def _target_source(actor, program_id, target):
    key = target.get('record_key', target.get('document_key'))
    kind = 'question' if target['kind'] == 'questions' else target['kind']
    return next((source for source in evidence._catalog(actor, program_id)
                 if source['kind'] == kind and source['record_key'] == key), None)


def _reviewed_fields(kind, fields):
    if kind == 'supporting':
        return {key:fields.get(key) for key in ('title','structured')}
    if kind == 'questions':
        keys = ('text', 'stage', 'answer_type', 'document_key', 'depends_on')
        return {**{key: fields.get(key, [] if key == 'depends_on' else None) for key in keys},
                'reason': fields.get('reason', ''), 'source_handles': fields.get('source_handles', [])}
    if kind == 'proposal':
        return {key: fields.get(key, [] if key == 'response_ids' else '')
                for key in ('title', 'text', 'theme', 'response_ids')}
    if kind == 'decision':
        return {key: fields.get(key, [] if key.endswith('_ids') else None)
                for key in ('outcome', 'rationale', 'supersedes_id', 'proposal_ids', 'response_ids')}
    return {key: value for key, value in fields.items() if key in {'sections', 'rows', 'decision_ids'}}


def _source_fields(source):
    content = source['content']
    fields = dict(content)
    if source['kind'] in {'proposal', 'decision'}:
        fields['response_ids'] = [link['response_id'] for link in fields['response_links']]
    return _reviewed_fields('questions' if source['kind'] == 'question' else source['kind'], fields)


def _fingerprint(manifest):
    # These revision numbers describe the entire program, not this source.
    # Their content hashes still detect actual edits, including decision values.
    return {key: value for key, value in manifest.items()
            if not (key == 'revision' and manifest['kind'] in {'program', 'document', 'decision_field'})}


def _attach_verified(actor, program_id, target, verified, saved_fields):
    """Attach only the prevalidated internal context; never recheck old sources.

    The write transaction excludes concurrent writers. Read accepted save
    fingerprints after its mutation so its own target edit is initially fresh.
    """
    if (not isinstance(verified, _Verified) or verified.connection is not get_db()
            or not get_db().in_transaction):
        raise RuntimeError('Origin attachment requires a verified domain save transaction')
    payload = verified.payload
    if payload['actor_id'] != actor.user_id or payload['program_id'] != program_id:
        _invalid()
    target = evidence.validate_target(target)
    request_target = payload['target']
    if (target['kind'] != request_target['kind']
            or target['kind'] == 'document' and target.get('document_key') != request_target.get('document_key')
            or target['kind'] in {'proposal', 'questions'} and 'record_key' in request_target
            and target.get('record_key') != request_target['record_key']
            or target['kind'] == 'decision' and 'record_key' in request_target
            and saved_fields.get('supersedes_id') != request_target['record_key']):
        _invalid()
    if target['kind'] == 'questions' and any(
            key in request_target and request_target[key] != saved_fields.get(key)
            for key in ('stage', 'document_key')):
        _invalid()
    source = _target_source(actor, program_id, target)
    if source is None or target['kind'] == 'review':
        raise DomainError('stale_evidence', 'This saved target is unavailable for origin attachment. Save reviewed clean wording manually.', 409)
    reviewed = _reviewed_fields(target['kind'], saved_fields)
    if evidence._hash(reviewed) != evidence._hash(_source_fields(source)):
        _invalid()
    available = {item['handle']: evidence._manifest(item) for item in evidence._catalog(actor, program_id)}
    saved_sources = [available.get(item['handle'], item) for item in payload['sources']]
    record = {'id': str(uuid4()), 'program_id': program_id, 'actor_id': actor.user_id,
              'target_kind': target['kind'], 'target_key': source['record_key'],
              'request_target': evidence._encode(payload['target']),
              'generated_hash': payload['generated_hash'], 'output_hash': payload['output_hash'],
              'reviewed_hash': evidence._hash(reviewed), 'target_hash': source['content_hash'],
              'model_alias': payload['model_alias'], 'generated_at': payload['generated_at'],
              'saved_at': utcnow(), 'sources': evidence._encode(payload['sources']),
              'saved_sources': evidence._encode(saved_sources)}
    columns = ','.join(record)
    get_db().execute(f"INSERT INTO ai_origins({columns}) VALUES ({','.join('?' for _ in record)})", tuple(record.values()))
    return _projection(record, source, available)


def attach_origin(actor: Principal, program_id: str, target: dict, receipt: str, saved_fields: dict) -> dict:
    """Attach to an existing unchanged record; domain saves use prevalidation.

    An external caller cannot replace a receipt with a fabricated verified dict.
    Normal save services own ID resolution and attach atomically after mutation.
    """
    with transaction():
        payload = validate_receipt(actor, program_id, target, receipt)
        return _attach_verified(actor, program_id, target, _Verified(payload, get_db()), saved_fields)


def _projection(row, source, available):
    result = dict(row)
    for key in ('request_target', 'sources', 'saved_sources'):
        result[key] = json.loads(result[key])
    result['target_changed'] = source is None or source['content_hash'] != result['target_hash']
    result['stale_sources'] = [manifest['handle'] for manifest in result['saved_sources']
                               if manifest['handle'] not in available
                               or evidence._encode(_fingerprint(manifest)) != evidence._encode(_fingerprint(available[manifest['handle']]))]
    result['stale'] = result['target_changed'] or bool(result['stale_sources'])
    return result


def origins(actor: Principal, program_id: str, target: dict) -> list[dict]:
    """Editor-only historical origins with dynamically checked changed support."""
    target = evidence.validate_target(target)
    require_role(actor, program_id, {'owner', 'facilitator'})
    key = target.get('record_key', target.get('document_key'))
    if key is None:
        return []
    available = {item['handle']: evidence._manifest(item) for item in evidence._catalog(actor, program_id)}
    source = _target_source(actor, program_id, target)
    return [_projection(row, source, available) for row in get_db().execute(
        'SELECT * FROM ai_origins WHERE program_id=? AND target_kind=? AND target_key=? ORDER BY saved_at,rowid',
        (program_id, target['kind'], key))]

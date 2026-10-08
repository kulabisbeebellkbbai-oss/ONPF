"""One bounded unsaved generation, with authority and evidence checked twice."""
from threading import BoundedSemaphore, Lock

from flask import current_app

from onpf.archives.service import contains_quarantined_copy
from onpf.auth.models import Principal
from onpf.auth.service import require_role
from onpf.db import get_db
from onpf.drafting import evidence, gateway, provenance
from onpf.drafting.config import validate_settings
from onpf.drafting.contracts import build_messages, output_format, validate_output
from onpf.errors import DomainError

_SLOT_CREATION_LOCK = Lock()


def _slot():
    with _SLOT_CREATION_LOCK:
        return current_app.extensions.setdefault('onpf_drafting_slot', BoundedSemaphore(1))


def _authorize(actor, program_id, target):
    require_role(actor, program_id, {'owner'} if target['kind'] == 'decision' else {'owner', 'facilitator'})


def _disclosure(value):
    if contains_quarantined_copy(value):
        raise DomainError('quarantined_content', 'Known removed copied text needs review before drafting. Remove it from the instructions and unsaved fields or generate a clean suggestion.', 409)


def _scoped_target(target):
    # A document is an existing scoped target even without an explicit record
    # key. Its framework key alone must not bypass record quarantine.
    return {**target, 'record_key': target['document_key']} if target['kind'] == 'document' else target


def _resolve_links(fields, manifests):
    # Only server-produced selected manifests supply record keys. Provider IDs
    # never become authority, including a valid-looking UUID in this program.
    selected = {manifest['handle']: manifest for manifest in manifests}
    result = dict(fields)
    for key, kind in (('response_ids', 'response'), ('proposal_ids', 'proposal'), ('decision_ids', 'decision')):
        if key in result:
            if any(selected[handle]['kind'] != kind for handle in result[key]):
                raise DomainError('invalid_ai_output', 'The suggestion contains an invalid source link. Generate again.', 502)
            result[key] = list(dict.fromkeys(selected[handle]['record_key'] for handle in result[key]))
    if result.get('supersedes_id') is not None:
        manifest = selected[result['supersedes_id']]
        if manifest['kind'] != 'decision':
            raise DomainError('invalid_ai_output', 'The suggestion contains an invalid source link. Generate again.', 502)
        result['supersedes_id'] = manifest['record_key']
    return result


def generate(actor: Principal, program_id: str, target: dict, handles: list[str], instructions: str, current_fields: dict, *, reviewed_sources=None) -> dict:
    """Return editable fields and ephemeral traceability; never save domain text."""
    target = evidence.validate_target(target)
    _authorize(actor, program_id, target)
    from onpf.programs.retirement import ensure_editable
    ensure_editable(program_id)
    from onpf.administration.service import require_ai
    require_ai(actor, program_id)
    try:
        settings = validate_settings(current_app.config)
    except ValueError:
        raise DomainError('ai_configuration', 'Your organization’s AI drafting service is unavailable. Ask the administrator for help; your wording is retained.', 503) from None
    if not settings['AI_DRAFTING_ENABLED']:
        raise DomainError('ai_disabled', 'AI drafting is disabled for this installation.', 503)
    slot = _slot()
    if not slot.acquire(blocking=False):
        raise DomainError('ai_busy', 'Another suggestion is being generated. Try again after it finishes.', 503)
    try:
        if get_db().in_transaction:
            raise DomainError('ai_transaction_open', 'Finish the current database operation before generating a suggestion.', 409)
        scoped_target = _scoped_target(target)
        selected = evidence.select(actor, program_id, scoped_target, handles)
        if reviewed_sources is not None and any(reviewed_sources.get(source['handle'])!=evidence._hash(source) for source in selected['sources']):
            raise DomainError('stale_evidence','Selected evidence changed before generation. Review the current previews and refresh sources. Your wording is retained.',409)
        messages = build_messages(target, selected, instructions, current_fields)
        _disclosure({'instructions': instructions, 'current_fields': current_fields})
        from onpf.drafting import usage
        import json
        resource=usage.limits(actor,program_id)
        if selected['evidence_bytes']>resource['evidence_bytes']:
            raise DomainError('ai_resource_limit','Selected evidence exceeds the scoped evidence limit.',422)
        settings['AI_TIMEOUT_SECONDS']=min(settings['AI_TIMEOUT_SECONDS'],resource['timeout_seconds'])
        settings['AI_MAX_OUTPUT_TOKENS']=min(settings['AI_MAX_OUTPUT_TOKENS'],resource['output_tokens'])
        response_format=output_format(target)
        request_bytes=len(json.dumps({'model':settings['AI_MODEL'],'messages':messages,
            'max_tokens':settings['AI_MAX_OUTPUT_TOKENS'],'stream':False,
            'response_format':response_format},ensure_ascii=False,allow_nan=False,
            separators=(',',':')).encode('utf8'))
        reservation=usage.reserve(actor,program_id,request_bytes,request_bytes,settings['AI_MAX_OUTPUT_TOKENS'])
        try:
            try:
                raw = gateway.complete(settings, messages, response_format)
            finally:
                usage.finish(reservation,getattr(locals().get('raw'), 'usage', None))
        except gateway.GatewayError:
            raise DomainError('ai_unavailable', 'Your organization’s AI service could not produce a suggestion. Your wording is retained; try again later or continue editing.', 503) from None
        if len(raw.encode('utf8'))>resource['output_bytes']:
            raise DomainError('ai_resource_limit','The suggestion exceeds the configured output limit. Your current draft is retained.',502)
        # Check before decoding or returning any newly generated text, including
        # for empty selections and owner decisions after an owner demotion.
        _authorize(actor, program_id, target)
        ensure_editable(program_id)
        evidence.assert_current(actor, program_id, selected['sources'], review=target['kind'] == 'review')
        if 'record_key' in scoped_target:
            try:
                evidence.catalog(actor, program_id, scoped_target)
            except DomainError as error:
                if error.code != 'invalid_target':
                    raise
                raise DomainError('stale_evidence', 'The drafting target became unavailable. Review the current form and sources before generating again.', 409) from None
        output = validate_output(target, raw, {source['handle'] for source in selected['sources']})
        _disclosure(output)
        result = {'fields': _resolve_links(output['fields'], selected['sources']),
                  'sources': selected['sources'], 'uncertainties': output['uncertainties'],
                  'questions': output['questions']}
        if target['kind'] == 'document':
            from onpf.drafting.guards import protect_sections, GUARD
            result['fields'] = protect_sections(result['fields'], current_fields, initial=target.get('mode') == 'initial')
            if target.get('mode') == 'regenerate':
                for key,text in result['fields']['sections'].items():
                    if text.strip() and 'REVIEW REQUIRED' not in text:
                        result['fields']['sections'][key] = GUARD + '\n\n' + text
        if target['kind'] == 'supporting':
            from onpf.drafting.guards import GUARD,outstanding
            prior=current_fields.get('structured',{}).get('blocks',[])
            for index,block in enumerate(result['fields']['structured']['blocks']):
                old=prior[index] if index<len(prior) else {}
                populated=bool(old.get('text','').strip() or old.get('label','').strip() or old.get('items') or old.get('rows'))
                if not populated or outstanding(old):
                    block['review_guard']=GUARD
            for index,block in enumerate(prior):
                if outstanding(block) and index>=len(result['fields']['structured']['blocks']):
                    result['fields']['structured']['blocks'].append(block)
        if target['kind']=='questions':
            for index,question in enumerate(result['fields'].get('questions',[])):
                old=(current_fields.get('questions') or [{}])[min(index,len(current_fields.get('questions') or [{}])-1)]
                for key in ('group_label','why','source_type','source_id'):
                    if key in old and key not in question:
                        question[key]=old[key]
        return {**result, 'receipt': provenance.issue_receipt(actor, program_id, target, selected['sources'], result)}
    finally:
        slot.release()

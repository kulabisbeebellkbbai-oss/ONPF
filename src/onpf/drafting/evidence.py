"""Explicit, authorized evidence projections and content-free source manifests.

No source text is stored or signed here. Handles identify a source within one
program; revisions and hashes are always recomputed through domain services.
"""
import hashlib
import json
import re
from uuid import UUID

from flask import current_app

from onpf.archives.service import contains_quarantined_copy, quarantined
from onpf.auth.models import Principal
from onpf.drafting.config import DEFAULTS
from onpf.errors import DomainError
from onpf.inquiries import service as inquiries
from onpf.materials import service as materials
from onpf.programs.framework import load_framework
from onpf.programs.service import get_program
from onpf.refinement import service as refinement

MAX_SOURCES = 100
TARGET_KINDS = frozenset({'questions', 'proposal', 'decision', 'document', 'review', 'supporting'})


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_encode(value).encode('utf-8')).hexdigest()


def _invalid_target():
    raise DomainError('invalid_target', 'Choose an available drafting target, stage and document.', 422)


def validate_target(target: dict) -> dict:
    """Validate reusable target syntax; catalog additionally verifies record scope.

    Only kind is universally required. Documents require document_key. Optional
    stage is an integer from 1 to 7. record_key can identify an existing question,
    proposal or decision, or repeat a document's key; review has no record key.
    Framework question and document keys are intentionally not UUID-only.
    """
    if not isinstance(target, dict) or set(target) - {'kind', 'stage', 'document_key', 'record_key', 'mode'}:
        _invalid_target()
    if 'mode' in target and (target.get('kind') != 'document' or target['mode'] not in {'initial','assist','regenerate'}):
        _invalid_target()
    kind = target.get('kind')
    if not isinstance(kind, str) or kind not in TARGET_KINDS:
        _invalid_target()
    if 'stage' in target and (type(target['stage']) is not int or target['stage'] not in range(1, 8)):
        _invalid_target()
    document_key = target.get('document_key')
    if 'document_key' in target and (not isinstance(document_key, str) or document_key not in load_framework()['documents']):
        _invalid_target()
    if kind == 'document' and document_key is None:
        _invalid_target()
    if 'record_key' in target:
        key = target['record_key']
        if not isinstance(key, str) or not key or len(key) > 100:
            _invalid_target()
        if kind in {'proposal', 'decision', 'supporting'}:
            try:
                if str(UUID(key)) != key:
                    _invalid_target()
            except ValueError:
                _invalid_target()
        elif kind == 'questions':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
                _invalid_target()
        elif kind != 'document' or key != document_key:
            _invalid_target()
    return dict(target)


def _source(program_id, kind, key, revision, label, content, **metadata):
    # Labels are fixed descriptions, not copied source titles. Thus the complete
    # manifest may be signed or persisted without persisting any authored text.
    return {'handle': f'{kind}:{program_id}:{key}', 'kind': kind, 'record_key': key,
            'revision': revision, 'content_hash': _hash(content), 'label': label,
            'content': content, **metadata}


def _fields(record, keys):
    return {key: record[key] for key in keys}


def _material_content(record, status):
    return {**_fields(record, ('title', 'content', 'ownership_basis', 'permission_basis', 'license', 'notices', 'source_version_id')),
            'status': status, 'source_terms': record['source_terms']}


def _lineage(record):
    return [{'kind': 'material_version', 'record_key': term['id'],
             'revision': term['version_number'], 'content_hash': _hash(term)}
            for term in record['source_terms']]


def _catalog(actor, program_id, *, review=False):
    program = get_program(actor, program_id)  # Authorizes before any source read.
    framework = load_framework()
    sources = []

    def add(kind, key, revision, label, content, *, table=None, row=None, **metadata):
        if (table and quarantined(table, row, program_id)) or contains_quarantined_copy(content):
            return
        sources.append(_source(program_id, kind, key, revision, label, content, **metadata))

    add('program', program_id, program['revision'], 'Program context',
        _fields(program, ('title', 'purpose', 'local_context', 'operating_status', 'module_keys')),
        table='programs', row=program)
    from onpf.drafting.organizer import current
    for statement in current(actor, program_id):
        add('organizer_clarification',statement['id'],statement['revision'],'Organizer clarification',
            {key:statement[key] for key in ('text','author_id','saved_at','context')})
    if review:
        from onpf.releases.service import drafting_lifecycle
        # A review-only live status projection. No archive/persistence consumer
        # opts into it; exact hashes catch changes without aggregate revisions.
        add('lifecycle', program_id, 1, 'Recorded design lifecycle', drafting_lifecycle(actor, program_id))
    for key, document in program['documents'].items():
        content = {**document, 'status': 'draft', 'decision_ids': program['document_decision_ids'].get(key, [])}
        add('document', key, program['revision'], f'Draft document: {key}', content,
            table='program_documents', row={'document_key': key, 'content': _encode(document)}, document_key=key)
    for field in inquiries.decision_fields(actor, program_id):
        add('decision_field', field['key'], program['revision'], 'Owner decision field',
            _fields(field, ('label', 'value', 'depends_on')))
    questions = inquiries.question_catalog(actor, program_id)
    for question in questions:
        add('question', question['id'], framework['version'], 'Draft inquiry question',
            _fields(question, ('text', 'stage', 'answer_type', 'document_key', 'depends_on', 'relevant', 'deferred_reason', 'reason', 'source_handles')),
            stage=question['stage'], document_key=question['document_key'],
            context_revision=question['context_revision'])
    issued = {}
    for listed in inquiries.list_batches(actor, program_id):
        batch = inquiries.get_batch(actor, listed['id'])
        round_context = {'round_kind': batch['kind'], 'stage': batch['stage'], 'reason': batch['reason'],
                         'source_handles': [source['handle'] for source in batch['sources']]} if 'kind' in batch else {}
        add('batch', batch['id'], batch['version'], 'Issued inquiry batch',
            {**_fields(batch, ('title', 'instructions', 'target_group', 'due_date', 'closed_at')), **round_context})
        for question in batch['questions']:
            issued[question['id']] = question
            context = {'reason': question['reason'], 'source_handles': [source['handle'] for source in question['sources']]} if 'reason' in question else {}
            # Read append-only metadata directly; calling rounds would recurse
            # through coverage/catalog. Exclude known quarantined copied reasons.
            from onpf.db import get_db
            deferrals = [dict(row) for row in get_db().execute('SELECT id,actor_id,deferred_at,reason FROM question_deferrals WHERE program_id=? AND batch_id=? AND question_id=? ORDER BY deferred_at,rowid',
                                                           (program_id, batch['id'], question['id']))]
            if deferrals:
                context['deferrals'] = [row for row in deferrals if not contains_quarantined_copy(row['reason'])]
            add('issued_question', question['id'], batch['version'], 'Issued inquiry question',
                {**_fields(question, ('source_id', 'stage', 'text', 'answer_type', 'document_key', 'depends_on', 'override_reason')), **context},
                stage=question['stage'], document_key=question['document_key'])
    for response in refinement.coverage(actor, program_id):
        if response['redaction'] is not None:
            continue
        question = issued[response['question_id']]
        content = _fields(response, ('text', 'answer_state', 'status', 'review_required', 'duplicate_of', 'question_id'))
        # Preserve the reason's revision association: coverage may retain an
        # older disposition after a correction makes the response unreviewed.
        # A quarantined reason is never disclosed with an authorized response.
        disposition = response['disposition']
        if disposition and not contains_quarantined_copy(disposition['reason']):
            content['disposition'] = {
                **_fields(disposition, ('response_revision_id', 'status', 'reason')),
                'applies_to_current_revision': (
                    disposition['response_revision_id'] == response['current_revision_id']
                    and not response['review_required']),
            }
        add('response', response['id'], response['revision'], 'Contributor response', content,
            response_revision_id=response['current_revision_id'], stage=question['stage'], document_key=question['document_key'])
    for proposal in refinement.list_proposals(actor, program_id):
        add('proposal', proposal['id'], proposal['revision'], 'Draft proposal',
            {**_fields(proposal, ('title', 'text', 'theme', 'response_links')), 'status': 'draft'}, table='proposals', row=proposal)
    decisions = refinement.list_decisions(actor, program_id)
    successors = {decision['supersedes_id']: decision['id'] for decision in decisions if decision['supersedes_id']}
    for decision in decisions:
        successor = successors.get(decision['id'])
        add('decision', decision['id'], 1, 'Owner decision',
            {**_fields(decision, ('outcome', 'rationale', 'supersedes_id', 'proposal_ids', 'response_links')),
             'superseded_by': successor, 'status': 'superseded' if successor else 'current'}, table='decisions', row=decision)
    # Saved support retains its exact historical response revision.
    from onpf.db import get_db
    responses = {s['record_key']: s for s in sources if s['kind'] == 'response'}
    historical = set()
    for parent in list(sources):
        if parent['kind'] not in {'proposal', 'decision'}:
            continue
        for link in parent['content'].get('response_links', []):
            original = responses.get(link['response_id'])
            revision_id = link['response_revision_id']
            if not original or original['response_revision_id'] == revision_id or revision_id in historical:
                continue
            row = get_db().execute('SELECT * FROM response_revisions WHERE id=? AND response_id=?', (revision_id, link['response_id'])).fetchone()
            if not row or contains_quarantined_copy(row['text']):
                continue
            content = {**original['content'], 'text': row['text'], 'historical': True,
                       'status': 'historical support; current response changed'}
            saved = _source(program_id, 'response', original['record_key'], row['revision'],
                            'Historical contributor response', content,
                            response_revision_id=revision_id, stage=original['stage'],
                            document_key=original['document_key'])
            saved['handle'] += '@' + revision_id
            sources.append(saved)
            historical.add(revision_id)
    for material in materials.list_materials(actor, program_id):
        add('material', material['id'], material['revision'], 'Draft reusable material',
            _material_content(material, 'draft'), table='materials', row=material, lineage=_lineage(material))
    from onpf.additional_documents.service import list_additional
    for material in list_additional(actor,program_id):
        add('supporting',material['id'],material['revision'],'Supporting material',
            {key:material[key] for key in ('title','structured','sections','is_template','reviewed','template_source_id','template_source_revision','ownership_basis','permission_basis','license','notices')})
    # Shared-library authorization alone is insufficient selection scope. Only
    # explicitly adopted versions belong in this program's catalog.
    for adoption in materials.list_adoptions(actor, program_id):
        if adoption['available']:
            version = adoption['version']
            add('material_version', version['id'], version['version_number'], 'Selected reusable material version',
                _material_content(version, 'released_material'), lineage=_lineage(version))
    # Build the primitive catalog once, then resolve only direct support. Question
    # references use local context version plus context-free fields, never final
    # context-derived hashes, making self and cyclic support finite.
    contexts = {q['id']: q for q in questions}
    for source in sources:
        if source['kind'] == 'question':
            source['intrinsic_hash'] = _hash({'content': source['content'],
                                               'context_revision': source['context_revision']})
    primitive = {source['handle']: _support_descriptor(source) for source in sources}
    for source in sources:
        if source['kind'] == 'question':
            context = contexts[source['record_key']]
            support = [{'recorded': _support_descriptor(item),
                        'current': primitive.get(item['handle'])}
                       for item in sorted(context['sources'], key=lambda item: item['handle'])]
            source['content']['context_revision'] = source['context_revision']
            source['content']['support_hash'] = _hash(support)
            source['content_hash'] = _hash(source['content'])
    return sources


def _support_descriptor(source):
    """Finite content-free identity; aggregate-only touches do not alter support."""
    if source['kind'] == 'question':
        return {key: source[key] for key in ('handle', 'intrinsic_hash', 'context_revision')}
    return {key: value for key, value in _manifest(source).items()
            if key != 'revision' or source['kind'] not in {'program', 'document', 'decision_field'}}


def catalog(actor: Principal, program_id: str, target: dict) -> list[dict]:
    """Return authorized evidence with target-priority UI selection hints.

    Hints never implicitly add sources to selection. Unrelated sources remain
    explicit additions. Quarantined/removed content is absent, including titles
    and inherited terms, even when private domain editors may still read it.
    """
    target = validate_target(target)
    sources = _catalog(actor, program_id, review=target['kind'] == 'review')
    if 'record_key' in target:
        kind = 'question' if target['kind'] == 'questions' else target['kind']
        if not any(source['kind'] == kind and source['record_key'] == target['record_key'] for source in sources):
            _invalid_target()
    documents = {target['document_key']} if 'document_key' in target else set()
    if 'stage' in target:
        documents.update(next(stage['document_keys'] for stage in load_framework()['stages'] if stage['number'] == target['stage']))
    for source in sources:
        preferred = (source['kind'] in {'program', 'lifecycle', 'organizer_clarification'} or
                     source.get('document_key') in documents or
                     'stage' in target and source.get('stage') == target['stage'] or
                     'record_key' in target and source['record_key'] == target['record_key'])
        source['default_selected'] = bool(preferred)
        source['is_addition'] = not preferred
    return sorted(sources, key=lambda source: (not source['default_selected'], source['handle']))


def _manifest(source):
    return {key: value for key, value in source.items() if key not in {'content', 'default_selected', 'is_addition'}}


def inherited_handles(sources, fields):
    """Carry selected workflow support into a fresh drafting handoff only."""
    available = {s['handle']: s for s in sources}
    selected = set()
    for key, kind in (('proposal_ids','proposal'), ('response_ids','response'), ('decision_ids','decision')):
        for record_id in fields.get(key, []):
            selected.update(s['handle'] for s in sources if s['kind'] == kind and s['record_key'] == record_id)
    for question in fields.get('questions', []):
        selected.update(question.get('source_handles', []))
    for handle in list(selected):
        source = available.get(handle)
        if source and source['kind'] in {'proposal', 'decision'}:
            for link in source['content'].get('response_links', []):
                for candidate in sources:
                    if candidate['kind'] == 'response' and candidate['record_key'] == link['response_id'] and candidate.get('response_revision_id') == link['response_revision_id']:
                        selected.add(candidate['handle'])
    return sorted(selected)


def _handles(handles):
    if not isinstance(handles, list):
        raise DomainError('invalid_sources', 'Choose source handles from this program catalog.', 422)
    if len(handles) > MAX_SOURCES:
        raise DomainError('too_many_sources', 'Select at most 100 evidence sources.', 422)
    if any(not isinstance(handle, str) or not handle or len(handle) > 250 for handle in handles) or len(set(handles)) != len(handles):
        raise DomainError('invalid_sources', 'Choose valid evidence sources without duplicates.', 422)


def select(actor: Principal, program_id: str, target: dict, handles: list[str]) -> dict:
    """Return content-free sources and canonical bounded evidence_json in memory.

    Byte limits apply to the complete serialized evidence; nothing is truncated.
    Selected source order is canonical and independent of checkbox submission.
    """
    available = {source['handle']: source for source in catalog(actor, program_id, target)}
    _handles(handles)
    if any(handle not in available for handle in handles):
        raise DomainError('invalid_sources', 'One or more selected sources are unavailable in this program.', 422)
    selected = [{key: value for key, value in available[handle].items() if key not in {'default_selected', 'is_addition'}}
                for handle in sorted(handles)]
    encoded = _encode({'sources': selected})
    size = len(encoded.encode('utf-8'))
    limit = current_app.config.get('AI_MAX_EVIDENCE_BYTES', DEFAULTS['AI_MAX_EVIDENCE_BYTES'])
    # Disabled drafting settings need not have been normalized by config.py.
    if isinstance(limit, str) and re.fullmatch(r'[0-9]{1,5}', limit):
        limit = int(limit)
    if type(limit) is not int or not 1024 <= limit <= DEFAULTS['AI_MAX_EVIDENCE_BYTES']:
        raise DomainError('invalid_evidence_limit', 'The evidence limit needs operator configuration.', 503)
    if size > limit:
        raise DomainError('evidence_too_large', 'Selected evidence exceeds the configured byte limit. Select fewer sources.', 422)
    return {'sources': [_manifest(source) for source in selected], 'evidence_json': encoded, 'evidence_bytes': size}


def assert_current(actor: Principal, program_id: str, manifests: list[dict], *, review=False) -> None:
    """Reauthorize and recompute every source fingerprint after provider work.

    Comparison also detects current response corrections, supersession, changed
    inherited terms, lost adoptions and removals without trusting program revision.
    """
    available = {source['handle']: _manifest(source) for source in _catalog(actor, program_id, review=review)}
    if not isinstance(manifests, list) or any(not isinstance(source, dict) for source in manifests):
        raise DomainError('invalid_sources', 'Use the original content-free source manifest.', 422)
    _handles([source.get('handle') for source in manifests])
    for source in manifests:
        current = available.get(source['handle'])
        try:
            # JSON equality preserves types: Python dict equality would accept
            # forged True or 1.0 in place of the integer revision 1.
            matches = current is not None and _encode(source) == _encode(current)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise DomainError('stale_evidence', 'Selected evidence changed or became unavailable. Review current sources and generate again.', 409)

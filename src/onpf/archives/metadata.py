"""Historical, content-free metadata checks; deliberately independent of live freshness.

Mutable historical text/status cannot generally be reconstructed. Identity, scope,
revision witnesses and immutable snapshots are validated without requiring current
adoption, membership, module selection or source availability.
"""
import json
import re

from onpf.archives.validation import digest, invalid
from onpf.drafting.evidence import validate_target
from onpf.programs.framework import load_framework

LABELS = {'program': 'Program context', 'question': 'Draft inquiry question',
          'decision_field': 'Owner decision field', 'batch': 'Issued inquiry batch',
          'issued_question': 'Issued inquiry question', 'response': 'Contributor response',
          'proposal': 'Draft proposal', 'decision': 'Owner decision',
          'material': 'Draft reusable material', 'material_version': 'Selected reusable material version',
          'supporting':'Supporting material','organizer_clarification':'Organizer clarification'}


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        invalid()


def validate_metadata(db):
    def rows(table):
        return [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
    def index(table):
        return {row['id']: row for row in rows(table)}
    programs, batches, issued = index('programs'), index('batches'), index('batch_questions')
    responses, revisions, submissions = index('responses'), index('response_revisions'), index('submissions')
    proposals, decisions, materials, versions = index('proposals'), index('decisions'), index('materials'), index('material_versions')
    framework = load_framework()
    prompts = {q['id']: q for module in framework['modules'].values() for q in module['prompts']}
    custom = {(r['program_id'], r['id']): json.loads(r['content']) for r in rows('inquiry_questions')}
    contexts = {(r['program_id'], r['question_id']): r for r in rows('question_contexts')}
    rounds = {r['batch_id']: r for r in rows('drafting_clarification_rounds')}
    round_questions = {r['question_id']: r for r in rows('clarification_round_questions')}
    def question(pid, key):
        result = custom.get((pid, key), prompts.get(key))
        if result is None: invalid()
        return result
    def scoped(record, pid):
        if record['program_id'] != pid: invalid()
        return record
    def response(pid, key):
        result = responses[key]
        scoped(submissions[result['submission_id']], pid)
        return result
    def positive(value, upper):
        if type(value) is not int or not 1 <= value <= upper: invalid()
    term_keys = ('id', 'material_id', 'version_number', 'title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'content_hash')
    imports = {r['material_id']: json.loads(r['source_json']) for r in rows('imported_material_sources')}
    def imported_terms(mid):
        value = imports.get(mid)
        return [{key: term[key] for key in term_keys} for term in [value] + value['source_terms']] if value else []
    def terms(record):
        result, cursor = [], record['source_version_id']
        while cursor:
            version = versions[cursor]
            result.append({key: version[key] for key in term_keys})
            result.extend(imported_terms(version['material_id']))
            cursor = version['source_version_id']
        return result + imported_terms(record.get('material_id', record['id']))
    def material_content(record):
        return {**{key: record[key] for key in ('title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'source_version_id')},
                'content': json.loads(record['content']), 'status': 'released_material', 'source_terms': terms(record)}
    def decision_content(key):
        record = decisions[key]
        content = {field: record[field] for field in ('outcome', 'rationale', 'supersedes_id')}
        content['proposal_ids'] = [r[0] for r in db.execute('SELECT proposal_id FROM decision_proposals WHERE decision_id=? ORDER BY ordinal', (key,))]
        content['response_links'] = [dict(r) for r in db.execute('SELECT response_id,response_revision_id FROM decision_responses WHERE decision_id=? ORDER BY ordinal', (key,))]
        return content
    def decision_hashes(key):
        successor = next((r['id'] for r in decisions.values() if r['supersedes_id'] == key), None)
        return [digest({**decision_content(key), 'superseded_by': item, 'status': 'superseded' if item else 'current'}) for item in (None, successor)]
    exclusions = [set()]
    for event in db.execute('SELECT id FROM redaction_events ORDER BY recorded_at,rowid'):
        hashes = {h for row in db.execute('SELECT field_hashes FROM quarantined_content WHERE event_id=?', (event[0],)) for h in json.loads(row[0])}
        exclusions.append(exclusions[-1] | hashes)
    def manifests(value, pid):
        if isinstance(value, str): value = json.loads(value)
        if not isinstance(value, list) or len(value) > 100: invalid()
        handles = []
        for source in value:
            if not isinstance(source, dict): invalid()
            kind, key = source['kind'], source['record_key']
            if not isinstance(kind, str) or not isinstance(key, str): invalid()
            required = {'handle', 'kind', 'record_key', 'revision', 'content_hash', 'label'}
            if kind in {'question', 'issued_question', 'response'}: required |= {'stage', 'document_key'}
            if kind == 'document': required.add('document_key')
            if kind == 'response': required.add('response_revision_id')
            if kind in {'material', 'material_version'}: required.add('lineage')
            if kind == 'question': required |= {'context_revision', 'intrinsic_hash'}
            expected_handle=f'{kind}:{pid}:{key}'
            if kind=='response' and source['label']=='Historical contributor response':
                expected_handle+='@'+source['response_revision_id']
            if set(source) != required or source['handle'] != expected_handle: invalid()
            handles.append(source['handle'])
            _hash(source['content_hash'])
            label = f'Draft document: {key}' if kind == 'document' else LABELS.get(kind)
            if kind=='response' and '@' in source['handle']:
                label='Historical contributor response'
            if label is None or source['label'] != label: invalid()
            revision = source['revision']
            if kind != 'question': positive(revision, 2147483647)
            if 'stage' in source and (type(source['stage']) is not int or source['stage'] not in range(1, 8)): invalid()
            if 'document_key' in source and source['document_key'] not in framework['documents']: invalid()
            if kind == 'program':
                if key != pid: invalid()
                positive(revision, programs[pid]['revision'])
            elif kind == 'document':
                if key != source['document_key'] or not db.execute('SELECT 1 FROM program_documents WHERE program_id=? AND document_key=?', (pid, key)).fetchone(): invalid()
                positive(revision, programs[pid]['revision'])
            elif kind == 'decision_field':
                if not db.execute('SELECT 1 FROM decision_fields WHERE program_id=? AND key=?', (pid, key)).fetchone(): invalid()
                positive(revision, programs[pid]['revision'])
            elif kind == 'question':
                question(pid, key)
                if type(revision) is not str or revision != framework['version']: invalid()
                version = source['context_revision']
                if type(version) is not int or not 0 <= version <= contexts.get((pid, key), {}).get('context_revision', 0): invalid()
                _hash(source['intrinsic_hash'])
            elif kind == 'response':
                record = response(pid, key)
                witness = revisions[source['response_revision_id']] if 'response_revision_id' in source else None
                if witness is None or witness['response_id'] != key or witness['revision'] != revision: invalid()
                q = issued[record['question_id']]
                if source['stage'] != q['stage'] or source['document_key'] != q['document_key']: invalid()
            elif kind=='organizer_clarification':
                record=db.execute('SELECT * FROM organizer_clarifications WHERE program_id=? AND id=? AND revision=?',(pid,key,revision)).fetchone()
                if not record: invalid()
                content={field:record[field] for field in ('text','author_id','saved_at')}
                content['context']=json.loads(record['context_json'])
                if source['content_hash']!=digest(content): invalid()
            elif kind=='supporting':
                record=db.execute('SELECT * FROM additional_documents WHERE program_id=? AND id=?',(pid,key)).fetchone()
                if not record: invalid()
                positive(revision,record['revision'])
                historical=db.execute('SELECT snapshot_json FROM additional_document_revisions WHERE document_id=? AND revision=?',(key,revision)).fetchone()
                witness=json.loads(historical[0]) if historical else dict(record)
                content={field:witness[field] for field in ('title','template_source_id','template_source_revision','ownership_basis','permission_basis','license','notices')}
                content.update(structured=json.loads(witness['structured_json']) if witness['structured_json'] else None,
                               sections=json.loads(witness['sections_json']),is_template=bool(witness['is_template']),reviewed=bool(witness['reviewed']))
                if source['content_hash']!=digest(content): invalid()
            elif kind in {'proposal', 'decision', 'material'}:
                record = scoped({'proposal': proposals, 'decision': decisions, 'material': materials}[kind][key], pid)
                positive(revision, record['revision'] if kind != 'decision' else 1)
                if kind == 'decision':
                    if source['content_hash'] not in decision_hashes(key): invalid()
            elif kind == 'batch':
                record = scoped(batches[key], pid)
                if revision != record['version']: invalid()
                content = {field: record[field] for field in ('title', 'instructions', 'target_group', 'due_date', 'closed_at')}
                if key in rounds:
                    context = rounds[key]
                    content.update(round_kind=context['kind'], stage=context['stage'], reason=context['reason'], source_handles=[s['handle'] for s in json.loads(context['sources'])])
                if source['content_hash'] not in [digest({**content, 'closed_at': closed}) for closed in (None, record['closed_at'])]: invalid()
            elif kind == 'issued_question':
                record = issued[key]
                batch = scoped(batches[record['batch_id']], pid)
                if revision != batch['version'] or source['stage'] != record['stage'] or source['document_key'] != record['document_key']: invalid()
                content = {field: record[field] for field in ('source_id', 'stage', 'text', 'answer_type', 'document_key', 'override_reason')}
                content['depends_on'] = json.loads(record['depends_on'])
                if key in round_questions:
                    context = round_questions[key]
                    content.update(reason=context['reason'], source_handles=[s['handle'] for s in json.loads(context['sources'])])
                deferrals = [dict(r) for r in db.execute('SELECT id,actor_id,deferred_at,reason FROM question_deferrals WHERE question_id=? ORDER BY deferred_at,rowid', (key,))]
                hashes = {digest(content)}
                for count in range(len(deferrals) + 1):
                    prefix = deferrals[:count]
                    hashes.add(digest({**content, 'deferrals': prefix}))
                    for quarantined in exclusions:
                        hashes.add(digest({**content, 'deferrals': [d for d in prefix if digest(d['reason']) not in quarantined]}))
                if source['content_hash'] not in hashes: invalid()
            elif kind == 'material_version':
                record = versions[key]
                if revision != record['version_number'] or source['content_hash'] != digest(material_content(record)): invalid()
                # A removed adoption has no durable scope witness. Do not invent
                # one or demand current adoption; immutable version content is
                # still independently verified and the limitation is documented.
            if kind in {'material', 'material_version'}:
                lineage = source['lineage']
                if not isinstance(lineage, list): invalid()
                expected = [{'kind': 'material_version', 'record_key': t['id'], 'revision': t['version_number'], 'content_hash': digest(t)} for t in terms(record)]
                if digest(lineage) != digest(expected): invalid()
        if len(handles) != len(set(handles)): invalid()
        return value
    for row in contexts.values():
        question(row['program_id'], row['question_id'])
        positive(row['context_revision'], 2147483647)
        manifests(row['sources'], row['program_id'])
    for row in rounds.values():
        batch = scoped(batches[row['batch_id']], row['program_id'])
        if row['issued_at'] != batch['issued_at'] or row['issued_by'] != batch['issued_by']: invalid()
        manifests(row['sources'], row['program_id'])
    for row in round_questions.values():
        round_ = rounds[row['batch_id']]
        if issued[row['question_id']]['batch_id'] != row['batch_id']: invalid()
        manifests(row['sources'], round_['program_id'])
    for row in rows('ai_origins'):
        pid, kind, key = row['program_id'], row['target_kind'], row['target_key']
        if kind == 'questions': question(pid, key)
        elif kind in {'proposal', 'decision'}: scoped({'proposal': proposals, 'decision': decisions}[kind][key], pid)
        elif kind == 'document':
            if not db.execute('SELECT 1 FROM program_documents WHERE program_id=? AND document_key=?', (pid, key)).fetchone(): invalid()
        elif kind=='supporting':
            if not db.execute('SELECT 1 FROM additional_documents WHERE program_id=? AND id=?',(pid,key)).fetchone(): invalid()
        target = validate_target(json.loads(row['request_target']))
        if target['kind'] != kind: invalid()
        if 'record_key' in target:
            expected = decisions[key]['supersedes_id'] if kind == 'decision' else key
            if target['record_key'] != expected: invalid()
            if kind == 'decision': scoped(decisions[expected], pid)
        if kind == 'document' and target['document_key'] != key: invalid()
        for field in ('generated_hash', 'output_hash', 'reviewed_hash', 'target_hash'): _hash(row[field])
        if kind == 'decision':
            content = decision_content(key)
            reviewed = {field: content[field] for field in ('outcome', 'rationale', 'supersedes_id', 'proposal_ids')}
            reviewed['response_ids'] = [link['response_id'] for link in content['response_links']]
            if row['target_hash'] not in decision_hashes(key) or row['reviewed_hash'] != digest(reviewed): invalid()
        original, saved = manifests(row['sources'], pid), manifests(row['saved_sources'], pid)
        if [s['handle'] for s in original] != [s['handle'] for s in saved]: invalid()
        if not row['model_alias'] or len(row['model_alias']) > 200: invalid()

    # Mutable reasons may have been explicitly repaired since quarantine. Scope
    # and event evidence survive; current content need not equal the old hash.
    from onpf.archives.service import CONTENT, _program
    for row in rows('quarantined_content'):
        if row['table_name'] not in {'question_contexts', 'drafting_clarification_rounds', 'clarification_round_questions', 'question_deferrals'}:
            continue
        table = row['table_name']
        key = CONTENT[table][0]
        matches = db.execute(f'SELECT * FROM {table} WHERE {key}=?', (row['record_key'],)).fetchall()
        if not any(_program(db, table, match) == row['program_id'] for match in matches): invalid()
        _hash(row['content_hash'])
        event = db.execute('SELECT report FROM redaction_events WHERE id=?', (row['event_id'],)).fetchone()
        witness = {'table': table, 'record_key': row['record_key'], 'program_id': row['program_id'],
                   'content_hash': row['content_hash'], 'field_hashes': json.loads(row['field_hashes'])}
        if witness not in json.loads(event[0])['affected_records']: invalid()

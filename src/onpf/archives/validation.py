"""Exact archive/schema/hash and durable historical-reference validation."""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from onpf.errors import DomainError

FORMAT_VERSION = 1
APPLICATION_VERSION = '0.3.0'
OMITTED = {'auth_sessions', 'login_attempts', 'drafting_attempts'}
LIMIT = 256 * 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def invalid():
    raise DomainError('invalid_backup', 'The private backup is unsupported, corrupt, or has invalid references. No database was restored.', 422)


def schema(connection):
    return {row['name']: [dict(column) for column in connection.execute(f'PRAGMA table_info("{row["name"]}")')]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}


def schema_versions():
    return [path.name for path in sorted((Path(__file__).parents[1] / 'migrations').glob('*.sql'))]


def load(path):
    from onpf.exports.importer import bounded_json
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(LIMIT + 1)
        return bounded_json(data, LIMIT)
    except (OSError, DomainError, ValueError, TypeError, RecursionError):
        invalid()


def validate_envelope(value, connection, *, application_version=APPLICATION_VERSION, versions=None):
    versions = schema_versions() if versions is None else versions
    fields = {'classification', 'format_version', 'application_version', 'schema_versions', 'created_at', 'tables', 'hashes', 'archive_hash'}
    if (not isinstance(value, dict) or set(value) != fields or value['classification'] != 'PRIVATE'
            or type(value['format_version']) is not int or value['format_version'] != FORMAT_VERSION
            or value['application_version'] != application_version or value['schema_versions'] != versions
            or not isinstance(value['created_at'], str)):
        invalid()
    if digest({key: item for key, item in value.items() if key != 'archive_hash'}) != value['archive_hash']:
        invalid()
    metadata = schema(connection)
    tables = value['tables']
    if not isinstance(tables, dict) or set(tables) != set(metadata) - OMITTED or not isinstance(value['hashes'], dict) or set(value['hashes']) != set(tables):
        invalid()
    for table, rows in tables.items():
        columns = metadata[table]
        if not isinstance(rows, list) or digest(rows) != value['hashes'][table]:
            invalid()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {column['name'] for column in columns}:
                invalid()
            for column in columns:
                item = row[column['name']]
                if item is None:
                    if column['notnull'] or column['pk']:
                        invalid()
                elif column['type'] == 'INTEGER':
                    if type(item) is not int:
                        invalid()
                elif not isinstance(item, str):
                    invalid()
                if item is not None and column['type']=='TEXT' and (column['name'] == 'id' or column['name'].endswith('_id')) and (table, column['name']) not in {('inquiry_questions', 'id'), ('batch_questions', 'source_id'), ('question_contexts', 'question_id'), ('clarification_question_sources', 'source_id'),('ai_policies','target_id'),('administrative_events','target_id')}:
                    try:
                        if str(UUID(item)) != item:
                            invalid()
                    except (ValueError, TypeError, AttributeError):
                        invalid()
                if item is not None and column['name'].endswith('_at'):
                    try:
                        if datetime.fromisoformat(item).utcoffset() != timedelta(0):
                            invalid()
                    except (ValueError, TypeError):
                        invalid()
            if table in ('invitations', 'review_drafts') and (row['token_hash'] != 'disabled:' + row['id'] or not row['revoked_at']):
                invalid()
    if sorted(row['version'] for row in tables['schema_migrations']) != versions:
        invalid()
    if 'ai_policies' in tables:
        try:
            validate_controls(tables)
        except (ValueError,TypeError,KeyError,RecursionError):
            invalid()
    return tables


def validate_controls(tables):
    """Validate scoped identities, bounded policy and integer audit identities."""
    from onpf.drafting.usage import MAXIMUMS
    users={row['id'] for row in tables['users']}
    programs={row['id'] for row in tables['programs']}
    def scoped(scope,target):
        if not (scope=='system' and target=='' or scope=='user' and target in users or scope=='project' and target in programs):
            invalid()
    for row in tables['ai_policies']:
        scoped(row['scope'],row['target_id'])
        policy=json.loads(row['limits_json'])
        if not isinstance(policy,dict) or set(policy)-set(MAXIMUMS)-{'priced_model'}:
            invalid()
        for key,value in policy.items():
            if key=='priced_model':
                if row['scope']!='system' or not isinstance(value,str) or len(value)>100:
                    invalid()
            else:
                minimum=0 if key in {'input_micro_per_1000','output_micro_per_1000','daily_budget_micro'} else 1
                if type(value) is not int or not minimum<=value<=MAXIMUMS[key]:
                    invalid()
                if key in {'input_micro_per_1000','output_micro_per_1000'} and row['scope']!='system':
                    invalid()
    for row in tables['administrative_events']:
        scoped(row['scope'],row['target_id'])
        if type(row['id']) is not int or row['id']<1 or row['action'] not in {'access','ai_switch','membership','creation','create_user','ai_limits'} or not isinstance(json.loads(row['changes_json']),dict):
            invalid()
    for row in tables['project_retirement_events']:
        if type(row['id']) is not int or row['id']<1 or row['action'] not in {'retire','reactivate_private'}:
            invalid()
    for row in tables['ai_usage']:
        if (row['state'] not in {'reserved','uncertain','complete','cancelled','price_discrepancy'} or
            row['day']!=row['started_at'][:10] or any(type(row[key]) is not int or row[key]<0 for key in ('reserved_micro','charged_micro')) or
            any(row[key] is not None and (type(row[key]) is not int or not 0<=row[key]<=MAXIMUMS['input_micro_per_1000']) for key in ('input_price','output_price'))):
            invalid()
        if row['usage_json'] is not None:
            usage=json.loads(row['usage_json'])
            if not isinstance(usage,dict) or any(type(usage.get(key)) is not int or not 0<=usage[key]<=10000000 for key in ('prompt_tokens','completion_tokens')):
                invalid()


def validate_database(db, *, include_ai=True):
    """Check frozen evidence against its historical links, never mutable current owners."""
    from onpf.exports.importer import validate_source
    from onpf.exports.projection import public_document
    from onpf.materials.service import validate_content
    from onpf.programs.framework import load_framework
    from onpf.programs.service import _document_content
    from onpf.additional_documents.service import _content as additional_content, _upload as validate_additional_upload, attachment_bytes
    from onpf.inquiries.service import _validate_fields, _validate_question
    try:
        if db.execute('PRAGMA foreign_key_check').fetchone() or db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            invalid()
        present = set(schema(db))
        optional = {'additional_documents', 'clarification_question_sources', 'decision_proposal_snapshots', 'publications'}
        def rows(table):
            if table in optional and table not in present:
                return []
            return [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
        def index(table):
            return {row['id']: row for row in rows(table)}
        programs, responses, revisions = index('programs'), index('responses'), index('response_revisions')
        submissions, batches = index('submissions'), index('batches')
        users, decisions, proposals = index('users'), index('decisions'), index('proposals')
        materials, versions = index('materials'), index('material_versions')
        for row in rows('program_documents'):
            content = json.loads(row['content'])
            if _document_content(row['document_key'], content) != content:
                invalid()
        for row in rows('additional_documents'):
            parsed = json.loads(row['sections_json'])
            value = {'template_key': row['template_key'], 'title': row['title'], 'sections': parsed,
                     'ownership_basis': row['ownership_basis'], 'permission_basis': row['permission_basis'],
                     'license': row['license'], 'notices': row['notices'],
                     'include_in_public': bool(row['include_in_public'])}
            value.update(structured=json.loads(row['structured_json']) if row.get('structured_json') else None,
                         is_template=bool(row.get('is_template',False)),reviewed=bool(row.get('reviewed',False)),
                         template_source_id=row.get('template_source_id'),template_source_revision=row.get('template_source_revision'))
            if additional_content(value) != value:
                invalid()
            if row['upload_data'] is not None:
                uploaded = attachment_bytes({'data': row['upload_data'], 'sha256': row['upload_sha256']})
                if validate_additional_upload(uploaded, row['upload_name']) != {key: row[key] for key in ('upload_name', 'upload_mime', 'upload_data', 'upload_sha256')}:
                    invalid()
        for program_id, row in programs.items():
            modules = json.loads(row['module_keys'])
            if not isinstance(modules, list) or len(modules) != len(set(modules)) or any(key == 'core' or key not in load_framework()['modules'] for key in modules):
                invalid()
            fields = {field['key']: {**field, 'depends_on': json.loads(field['depends_on'])} for field in rows('decision_fields') if field['program_id'] == program_id}
            _validate_fields(fields)
            for question in rows('inquiry_questions'):
                if question['program_id'] == program_id:
                    _validate_question(json.loads(question['content']), fields)
        for source in rows('clarification_question_sources'):
            round_row = db.execute('SELECT c.program_id FROM clarification_rounds c JOIN batch_questions q ON q.batch_id=c.batch_id WHERE q.id=?', (source['question_id'],)).fetchone()
            if round_row is None or not source['why'].strip() or len(source['why']) > 2000 or len(source['group_label']) > 200:
                invalid()
            program_id, kind, identifier = round_row['program_id'], source['source_type'], source['source_id']
            if kind == 'program':
                valid = identifier == program_id
            elif kind == 'response':
                valid = db.execute('SELECT 1 FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE r.id=? AND s.program_id=?', (identifier, program_id)).fetchone()
            elif kind in ('proposal', 'decision'):
                table = 'proposals' if kind == 'proposal' else 'decisions'
                valid = db.execute(f'SELECT 1 FROM {table} WHERE id=? AND program_id=?', (identifier, program_id)).fetchone()
            elif kind == 'document':
                document, separator, section = identifier.partition(':')
                valid = separator and any(item['key'] == section for item in load_framework()['documents'].get(document, {}).get('sections', []))
            else:
                valid = False
            if not valid:
                invalid()
        imported_terms = {}
        for row in rows('imported_packages'):
            source = validate_source(json.loads(row['source_json']))
            imported_terms[row['program_id']] = source
        for row in rows('imported_material_sources'):
            source = json.loads(row['source_json'])
            if materials[row['material_id']]['program_id'] != row['program_id'] or source not in imported_terms[row['program_id']]['materials']:
                invalid()
        for row in responses.values():
            history = sorted((rev for rev in revisions.values() if rev['response_id'] == row['id']), key=lambda rev: rev['revision'])
            if [rev['revision'] for rev in history] != list(range(1, row['revision'] + 1)) or history[0]['text'] != row['text']:
                invalid()
        response_program = lambda response_id: submissions[responses[response_id]['submission_id']]['program_id']
        for row in submissions.values():
            if batches[row['batch_id']]['program_id'] != row['program_id']:
                invalid()
        for table, parent, column in [('proposal_responses', proposals, 'proposal_id'), ('decision_responses', decisions, 'decision_id')]:
            for row in rows(table):
                if parent[row[column]]['program_id'] != response_program(row['response_id']):
                    invalid()
        for row in rows('response_duplicates'):
            if response_program(row['response_id']) != response_program(row['original_id']):
                invalid()
        for table, parent, column in [('decision_proposals', decisions, 'decision_id')]:
            for row in rows(table):
                if parent[row[column]]['program_id'] != proposals[row['proposal_id']]['program_id']:
                    invalid()
        proposal_snapshots = {(item['decision_id'], item['proposal_id']): item for item in rows('decision_proposal_snapshots')}
        proposal_links = {(item['decision_id'], item['proposal_id']) for item in rows('decision_proposals')}
        if 'decision_proposal_snapshots' in present and set(proposal_snapshots) != proposal_links:
            invalid()
        for item in proposal_snapshots.values():
            if (item['proposal_revision'] < 1 or not item['title'].strip() or not item['text'].strip()
                    or item['capture_basis'] not in ('at_decision', 'migration_current')):
                invalid()
        dispositions = index('dispositions')
        for row in rows('disposition_proposals'):
            if response_program(dispositions[row['disposition_id']]['response_id']) != proposals[row['proposal_id']]['program_id']:
                invalid()
        for table in ('materials', 'material_versions'):
            for row in rows(table):
                content = validate_content(json.loads(row['content']))
                if table == 'material_versions' and hashlib.sha256(row['content'].encode('utf-8')).hexdigest() != row['content_hash']:
                    invalid()
                seen, cursor = {row['id']} if table == 'material_versions' else set(), row['source_version_id']
                while cursor:
                    if cursor in seen:
                        invalid()
                    seen.add(cursor)
                    source = versions[cursor]
                    if source['notices'] not in row['notices']:
                        invalid()
                    cursor = source['source_version_id']
                material_id = row.get('material_id', row['id'])
                provenance = db.execute('SELECT source_json FROM imported_material_sources WHERE material_id=?', (material_id,)).fetchone()
                if provenance:
                    source = json.loads(provenance[0])
                    if any(term['notices'] not in row['notices'] for term in [source] + source['source_terms']):
                        invalid()
        candidates = index('release_candidates')
        candidate_owners = rows('candidate_owners')
        approvals = rows('candidate_approvals')
        framework = load_framework()
        term_fields = ('id', 'material_id', 'version_number', 'title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'content_hash')
        def imported(material_id):
            row = db.execute('SELECT source_json FROM imported_material_sources WHERE material_id=?', (material_id,)).fetchone()
            if row is None:
                return []
            source = json.loads(row[0])
            return [{key: term[key] for key in term_fields} for term in [source] + source['source_terms']]
        def source_terms(version):
            result, cursor = [], version['source_version_id']
            while cursor:
                source = versions[cursor]
                result.append({key: source[key] for key in term_fields})
                result.extend(imported(source['material_id']))
                cursor = source['source_version_id']
            return result + imported(version['material_id'])
        for row in candidates.values():
            snap = json.loads(row['snapshot'])
            if (digest(snap) != row['content_hash'] or snap['schema_version'] != 1 or snap['program_id'] != row['program_id']
                    or snap['program_revision'] != row['program_revision'] or snap['approval_rule'] not in ('all', 'any')
                    or snap['framework']['version'] != framework['version']):
                invalid()
            owners = sorted(item['user_id'] for item in candidate_owners if item['candidate_id'] == row['id'])
            if not owners or snap['owner_ids'] != owners or set(snap['owner_labels']) != set(owners) or any(owner not in users for owner in owners):
                invalid()
            if snap['response_revision_ids'] != [item['response_revision_id'] for item in snap['coverage']]:
                invalid()
            for item in snap['coverage']:
                revision = revisions[item['response_revision_id']]
                if revision['response_id'] != item['response_id'] or response_program(item['response_id']) != row['program_id']:
                    invalid()
                if item['disposition_id']:
                    disposition = dispositions[item['disposition_id']]
                    if disposition['response_id'] != item['response_id'] or disposition['response_revision_id'] != revision['id']:
                        invalid()
                if any(proposals[key]['program_id'] != row['program_id'] for key in item['proposal_ids']):
                    invalid()
                if item['duplicate_of'] and response_program(item['duplicate_of']) != row['program_id']:
                    invalid()
            if snap['material_version_ids'] != [item['id'] for item in snap['materials']]:
                invalid()
            for item in snap['materials']:
                version = versions[item['id']]
                if any(item[key] != (json.loads(version[key]) if key == 'content' else version[key]) for key in version):
                    invalid()
                if item['source_terms'] != source_terms(version):
                    invalid()
            frozen_decision_ids = {item['id'] for item in snap['decisions']}
            support = snap['document_decision_ids']
            if (len(frozen_decision_ids) != len(snap['decisions']) or not isinstance(support, dict)
                    or set(support) - set(snap['documents'])
                    or any(not isinstance(links, list) or any(not isinstance(link, str) or link not in frozen_decision_ids for link in links) or len(links) != len(set(links)) for links in support.values())):
                invalid()
            for item in snap['decisions']:
                original = decisions[item['id']]
                if original['program_id'] != row['program_id'] or any(item[key] != value for key, value in original.items()):
                    invalid()
                links = [dict(link) for link in db.execute('SELECT response_id,response_revision_id FROM decision_responses WHERE decision_id=? ORDER BY ordinal', (item['id'],))]
                proposal_ids = [link[0] for link in db.execute('SELECT proposal_id FROM decision_proposals WHERE decision_id=? ORDER BY ordinal', (item['id'],))]
                if item['response_links'] != links or item['response_ids'] != [link['response_id'] for link in links] or item['proposal_ids'] != proposal_ids:
                    invalid()
                if 'proposal_snapshots' in item:
                    expected_proposals = [proposal_snapshots[(item['id'], proposal_id)] for proposal_id in proposal_ids]
                    if item['proposal_snapshots'] != expected_proposals:
                        invalid()
                for link in item['response_links']:
                    if revisions[link['response_revision_id']]['response_id'] != link['response_id'] or response_program(link['response_id']) != row['program_id']:
                        invalid()
            # Reuse the public schema validator for frozen documents/templates/material terms.
            source = public_document({**snap, 'id': row['id'], 'release_number': 1, 'released_at': row['prepared_at'], 'content_hash': row['content_hash'], 'state': 'released'})
            validate_source(source)
        for approval in approvals:
            if approval['content_hash'] != candidates[approval['candidate_id']]['content_hash']:
                invalid()
        for release in rows('releases'):
            candidate = candidates[release['candidate_id']]
            snap = json.loads(candidate['snapshot'])
            stored = json.loads(release['approvals'])
            expected = [{key: item[key] for key in ('user_id', 'content_hash', 'approved_at')} for item in approvals if item['candidate_id'] == candidate['id']]
            if (release['snapshot'] != candidate['snapshot'] or release['content_hash'] != candidate['content_hash']
                    or sorted(stored, key=lambda item: item['user_id']) != sorted(expected, key=lambda item: item['user_id']) or not stored
                    or (snap['approval_rule'] == 'all' and {item['user_id'] for item in stored} != set(snap['owner_ids']))):
                invalid()
        for row in rows('publications'):
            source = json.loads(row['source_json'])
            if (hashlib.sha256(row['source_json'].encode('utf-8')).hexdigest() != row['source_hash']
                    or source['publication_id'] != row['id'] or source['program_id'] != row['program_id']
                    or source['publication_version'] != row['version_number']
                    or source['program_revision'] != row['program_revision']
                    or source['approval_status'] != row['approval_status']):
                invalid()
            for item in source.get('additional_documents', []):
                if item['attachment']:
                    uploaded = attachment_bytes(item['attachment'])
                    if validate_additional_upload(uploaded, item['attachment']['name'])['upload_mime'] != item['attachment']['mime']:
                        invalid()
        for event in rows('redaction_events'):
            response = responses[event['response_id']]
            if response_program(response['id']) != event['program_id'] or response['text'] or response['display_name'] or response['entered_by'] or response['publication_permission'] != 'none':
                invalid()
            if any(row['text'] or row['reason'] or row['entered_by'] for row in revisions.values() if row['response_id'] == response['id']):
                invalid()
            json.loads(event['report'])
        for row in rows('quarantined_content'):
            from onpf.archives.service import CONTENT
            if row['table_name'] not in CONTENT:
                invalid()
            hashes = json.loads(row['field_hashes'])
            if not isinstance(hashes, list) or not hashes or any(not isinstance(value, str) or len(value) != 64 or any(char not in '0123456789abcdef' for char in value) for value in hashes):
                invalid()
        if include_ai and 'ai_origins' in present:
            from onpf.archives.metadata import validate_metadata
            validate_metadata(db)
        if 'ai_policies' in present:
            validate_controls({table:rows(table) for table in ('users','programs','ai_policies','administrative_events','project_retirement_events','ai_usage')})
    except DomainError as error:
        if error.code == 'invalid_backup':
            raise
        invalid()
    except (KeyError, TypeError, ValueError, IndexError, RecursionError):
        invalid()

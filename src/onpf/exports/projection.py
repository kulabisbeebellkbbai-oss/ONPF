"""Allowlist the frozen release's publishable editable design fields."""
from onpf.db import Record
from onpf.errors import DomainError

DOCUMENT_KEYS = ('overview', 'delivery', 'budget', 'volunteers', 'session-plan', 'feedback', 'adoption')
ROW_FIELDS = ('item', 'quantity', 'unit_cost', 'cost_status', 'notes')
TERM_FIELDS = ('id', 'material_id', 'version_number', 'title', 'ownership_basis', 'permission_basis', 'license', 'notices', 'content_hash')
MATERIAL_FIELDS = TERM_FIELDS + ('source_version_id',)
FORMAT = 'onpf-public-package'
SCHEMA_VERSION = 1


def public_design(record: Record, framework: Record, frozen_materials: list[Record], additional_documents: list[Record] | None = None) -> Record:
    """Allowlist only public design fields from a frozen release or workspace."""
    if set(record['documents']) != set(DOCUMENT_KEYS) or set(framework['documents']) != set(DOCUMENT_KEYS):
        raise DomainError('unsupported_documents', 'This public package format requires the seven supported document keys.', 422)
    documents, templates = {}, {}
    for key in DOCUMENT_KEYS:
        structure = framework['documents'][key]
        content = record['documents'][key]
        allowed_sections = {section['key'] for section in structure['sections']}
        if set(content['sections']) - allowed_sections:
            raise DomainError('unsupported_documents', 'A document contains unsupported section keys.', 422)
        documents[key] = {'title': structure['title'], 'sections': [
            {'key': section['key'], 'label': section['label'], 'text': content['sections'].get(section['key'], '')}
            for section in structure['sections']]}
        templates[key] = {'title': structure['title'], 'sections': [
            {field: section[field] for field in ('key', 'label', 'guidance')} for section in structure['sections']]}
        if key == 'budget':
            documents[key]['rows'] = [{field: row[field] for field in ROW_FIELDS} for row in content.get('rows', [])]
    materials = []
    for frozen in frozen_materials:
        material = {field: frozen[field] for field in MATERIAL_FIELDS}
        # validate_content constructs the defined literal-text schema; no private keys.
        from onpf.materials.service import validate_content
        material['content'] = validate_content(frozen['content'])
        material['source_terms'] = [{field: term[field] for field in TERM_FIELDS} for term in frozen['source_terms']]
        materials.append(material)
    result = {'title': record['title'], 'purpose': record['purpose'], 'local_context': record['local_context'],
            'operating_status': record['operating_status'], 'module_keys': list(record['module_keys']),
            'framework_version': framework['version'], 'templates': templates,
            'documents': documents, 'materials': materials}
    if additional_documents is not None:
        result['additional_documents'] = additional_documents
    return result


def public_document(release: Record) -> Record:
    """No contribution, identity, approval, or decision fields enter release exports."""
    if release.get('state') != 'released':
        raise DomainError('unavailable_release', 'Only an available approved release can be exported.', 409)
    return {'format': FORMAT, 'schema_version': SCHEMA_VERSION,
            **public_design(release, release['framework'], release['materials']),
            'provenance': {'release_id': release['id'], 'program_id': release['program_id'],
                           'release_number': release['release_number'], 'released_at': release['released_at'],
                           'source_snapshot_hash': release['content_hash']}}

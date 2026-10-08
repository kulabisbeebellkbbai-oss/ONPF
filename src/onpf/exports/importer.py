"""Bounded public ZIP/JSON validation, followed by one atomic draft import."""
import hashlib
import json
import re
import stat
import zlib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, BadZipFile

from onpf.db import Record, get_db, transaction, utcnow
from onpf.errors import DomainError
from onpf.exports.package import member_names, safe_id
from onpf.exports.projection import DOCUMENT_KEYS, FORMAT, MATERIAL_FIELDS, TERM_FIELDS
from onpf.exports.odt import validate_literal_text
from onpf.materials.service import save_material, validate_content
from onpf.programs.framework import load_framework
from onpf.programs.service import _account, create_program, get_program, save_document

UPLOAD_LIMIT = 25 * 1024 * 1024
UNCOMPRESSED_LIMIT = 100 * 1024 * 1024
JSON_LIMIT = 8 * 1024 * 1024
MANIFEST_LIMIT = 1024 * 1024
MEMBER_LIMIT = 512
JSON_DEPTH_LIMIT = 32


def _invalid():
    raise DomainError('invalid_package', 'This archive is not a supported, intact public ONPF package.', 422)


def _record(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        _invalid()


def _text(value, limit=50000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()) or any(ord(char) < 32 and char not in '\t\n\r' for char in value):
        _invalid()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        _invalid()


def _integer(value):
    if type(value) is not int or value < 1 or value > 2147483647:
        _invalid()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def bounded_json(data, limit=JSON_LIMIT):
    if len(data) > limit:
        _invalid()
    # Scan strings/escapes before json.loads, preventing deep parser recursion.
    depth, quoted, escaped = 0, False, False
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > JSON_DEPTH_LIMIT:
                _invalid()
        elif byte in (93, 125):
            depth -= 1
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=lambda value: _invalid())
    except (ValueError, UnicodeError, RecursionError):
        _invalid()


def _term(value):
    _record(value, TERM_FIELDS)
    safe_id(value['id'])
    safe_id(value['material_id'])
    _integer(value['version_number'])
    _digest(value['content_hash'])
    for key, maximum in [('title', 200), ('permission_basis', 5000), ('license', 5000), ('notices', 50000)]:
        _text(value[key], maximum, key != 'notices')
    if value['ownership_basis'] not in ('own_work', 'third_party'):
        _invalid()


def validate_source(source):
    validate_literal_text(source)
    _record(source, {'format', 'schema_version', 'title', 'purpose', 'local_context', 'operating_status', 'module_keys', 'framework_version', 'templates', 'documents', 'materials', 'provenance'})
    if source['format'] != FORMAT or type(source['schema_version']) is not int or source['schema_version'] != 1:
        _invalid()
    for key, maximum in [('title', 200), ('purpose', 50000), ('local_context', 50000), ('framework_version', 100)]:
        _text(source[key], maximum, key in ('title', 'framework_version'))
    framework = load_framework()
    if source['framework_version'] != framework['version']:
        raise DomainError('unsupported_framework', 'This package uses an unsupported framework version.', 422)
    if source['operating_status'] not in ('pending', 'confirmed', 'not_applicable'):
        _invalid()
    modules = source['module_keys']
    if not isinstance(modules, list) or len(modules) > 50 or any(not isinstance(key, str) or key == 'core' or key not in framework['modules'] for key in modules) or len(modules) != len(set(modules)):
        _invalid()
    _record(source['provenance'], {'release_id', 'program_id', 'release_number', 'released_at', 'source_snapshot_hash'})
    for key in ('release_id', 'program_id'):
        safe_id(source['provenance'][key])
    _integer(source['provenance']['release_number'])
    _text(source['provenance']['released_at'], 100, True)
    _digest(source['provenance']['source_snapshot_hash'])
    _record(source['documents'], DOCUMENT_KEYS)
    _record(source['templates'], DOCUMENT_KEYS)
    for key in DOCUMENT_KEYS:
        document, template = source['documents'][key], source['templates'][key]
        _record(template, {'title', 'sections'})
        _text(template['title'], 200, True)
        if not isinstance(template['sections'], list) or len(template['sections']) > 100:
            _invalid()
        expected_sections = {section['key'] for section in framework['documents'][key]['sections']}
        keys = []
        for section in template['sections']:
            _record(section, {'key', 'label', 'guidance'})
            for field, maximum in [('key', 100), ('label', 200), ('guidance', 50000)]:
                _text(section[field], maximum, field != 'guidance')
            keys.append(section['key'])
        if set(keys) != expected_sections or len(keys) != len(set(keys)):
            raise DomainError('unsupported_documents', 'The package contains unsupported document section keys.', 422)
        _record(document, {'title', 'sections', 'rows'} if key == 'budget' else {'title', 'sections'})
        if document['title'] != template['title'] or not isinstance(document['sections'], list):
            _invalid()
        validated = validate_content({'schema_version': 1, 'kind': 'document_bundle', 'documents': {key: document}})['documents'][key]
        if [section['key'] for section in validated['sections']] != keys or [section['label'] for section in validated['sections']] != [section['label'] for section in template['sections']]:
            _invalid()
    materials = source['materials']
    if not isinstance(materials, list) or len(materials) > 100:
        _invalid()
    ids = set()
    for material in materials:
        _record(material, set(MATERIAL_FIELDS) | {'content', 'source_terms'})
        _term({key: material[key] for key in TERM_FIELDS})
        if material['id'] in ids:
            _invalid()
        ids.add(material['id'])
        if material['source_version_id'] is not None:
            safe_id(material['source_version_id'])
        validate_content(material['content'])
        canonical = json.dumps(material['content'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if hashlib.sha256(canonical).hexdigest() != material['content_hash']:
            _invalid()
        if not isinstance(material['source_terms'], list) or len(material['source_terms']) > 100:
            _invalid()
        for term in material['source_terms']:
            _term(term)
            if term['notices'] not in material['notices']:
                _invalid()
        term_ids = [term['id'] for term in material['source_terms']]
        if len(set(term_ids)) != len(term_ids) or material['id'] in term_ids or (material['source_version_id'] is not None and (not term_ids or term_ids[0] != material['source_version_id'])):
            _invalid()
    return source


def validate_archive(archive_path):
    path = Path(archive_path)
    try:
        if path.stat().st_size > UPLOAD_LIMIT:
            raise DomainError('oversize_archive', 'The public package upload limit is 25 MiB.', 422)
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MEMBER_LIMIT or sum(info.file_size for info in members) > UNCOMPRESSED_LIMIT:
                _invalid()
            names = [info.filename for info in members]
            if len(names) != len(set(names)) or 'manifest.json' not in names or 'source/program.json' not in names:
                _invalid()
            for info in members:
                name = info.filename
                if (not name or '\\' in name or ':' in name or name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/'))
                        or str(PurePosixPath(name)) != name or stat.S_ISLNK(info.external_attr >> 16)
                        or info.is_dir() or info.flag_bits & 1 or info.compress_type not in (0, 8)):
                    _invalid()
            def read_bounded(name, limit):
                if archive.getinfo(name).file_size > limit:
                    _invalid()
                with archive.open(name) as stream:
                    value = stream.read(limit + 1)
                if len(value) > limit:
                    _invalid()
                return value
            manifest = bounded_json(read_bounded('manifest.json', MANIFEST_LIMIT), MANIFEST_LIMIT)
            _record(manifest, {'format', 'schema_version', 'framework_version', 'provenance', 'public_source_sha256', 'files'})
            if manifest['format'] != FORMAT or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1 or not isinstance(manifest['files'], dict) or set(manifest['files']) != set(names) - {'manifest.json'}:
                _invalid()
            total = 0
            for name, digest in manifest['files'].items():
                _digest(digest)
                hasher = hashlib.sha256()
                with archive.open(name) as stream:
                    while chunk := stream.read(65536):
                        total += len(chunk)
                        if total > UNCOMPRESSED_LIMIT:
                            _invalid()
                        hasher.update(chunk)
                if hasher.hexdigest() != digest:
                    _invalid()
            source_bytes = read_bounded('source/program.json', JSON_LIMIT)
            _digest(manifest['public_source_sha256'])
            if hashlib.sha256(source_bytes).hexdigest() != manifest['public_source_sha256']:
                _invalid()
            source = validate_source(bounded_json(source_bytes))
            if manifest['framework_version'] != source['framework_version'] or manifest['provenance'] != source['provenance'] or set(manifest['files']) != member_names(source):
                _invalid()
            # Only bounded declared JSON source is parsed; ODT/XML/CSV/MD remain opaque.
            for material in source['materials']:
                copy = bounded_json(read_bounded(f"materials/{material['id']}.json", JSON_LIMIT))
                if copy != material:
                    _invalid()
            return source
    except (BadZipFile, OSError, KeyError, TypeError, ValueError, RuntimeError, NotImplementedError, zlib.error):
        _invalid()


def import_program(actor, archive_path: Path, title: str) -> Record:
    _account(actor)  # Same authority as creation; sole ownership is established below.
    source = validate_archive(archive_path)
    _text(title, 200, True)
    hasher = hashlib.sha256()
    with Path(archive_path).open('rb') as stream:
        while chunk := stream.read(65536):
            hasher.update(chunk)
    with transaction() as connection:
        program = create_program(actor, {'title': title, 'purpose': source['purpose'], 'local_context': source['local_context'],
                                         'module_keys': source['module_keys'], 'operating_status': 'pending', 'approval_rule': 'all'})
        connection.execute('INSERT INTO imported_packages(program_id,source_json,package_sha256,imported_by,imported_at) VALUES (?,?,?,?,?)',
                           (program['id'], json.dumps(source, ensure_ascii=False), hasher.hexdigest(), actor.user_id, utcnow()))
        for key in DOCUMENT_KEYS:
            document = source['documents'][key]
            content = {'sections': {section['key']: section['text'] for section in document['sections']}}
            if key == 'budget':
                content['rows'] = document['rows']
            program = save_document(actor, program['id'], key, content, program['revision'])
        for material in source['materials']:
            draft = save_material(actor, program['id'], {key: material[key] for key in ('title', 'content', 'ownership_basis', 'permission_basis', 'license', 'notices')}, None)
            connection.execute('INSERT INTO imported_material_sources(material_id,program_id,source_json) VALUES (?,?,?)', (draft['id'], program['id'], json.dumps(material, ensure_ascii=False)))
        return get_program(actor, program['id'])

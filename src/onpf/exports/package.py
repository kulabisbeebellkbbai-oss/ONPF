"""Self-contained public files, checksums, and atomic ZIP publication."""
import hashlib
import json
import os
import tempfile
from importlib.resources import files as resource_files
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED

from onpf.auth.service import require_role
from onpf.db import transaction
from onpf.errors import DomainError
from onpf.exports.csv import render_csv
from onpf.exports.markdown import literal, render_markdown
from onpf.exports.odt import render_odt
from onpf.exports.projection import DOCUMENT_KEYS, FORMAT, ROW_FIELDS, public_document
from onpf.releases.service import get_release

MIT_0_LICENSE = resource_files('onpf').joinpath('static/LICENSE.txt').read_text(encoding='utf-8')


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def safe_id(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise DomainError('invalid_package', 'The public package contains an invalid source identifier.', 422)
    return value


def member_names(source):
    names = {'LICENSE', 'NOTICES.md', 'RELEASE-NOTES.md', 'README.md', 'source/program.json', 'tables/budget.csv', 'templates/session-plan.md', 'templates/session-plan.odt'}
    for key in DOCUMENT_KEYS:
        names.update({f'documents/{key}.md', f'documents/{key}.odt'})
    for material in source['materials']:
        names.add(f"materials/{safe_id(material['id'])}.json")
    return names


def blank_session(source):
    template = source['templates']['session-plan']
    return {'title': template['title'] + ' - blank local worksheet', 'sections': [{'key': section['key'], 'label': section['label'], 'text': ''} for section in template['sections']]}


def display_document(source, key):
    document = source['documents'][key]
    context = [{'key': 'program', 'label': 'Program and release', 'text': f"{source['title']}\nRelease {source['provenance']['release_number']} - framework {source['framework_version']}"},
               {'key': 'permission', 'label': 'Local operating permission', 'text': f"Recorded status: {source['operating_status']}. Design approval does not grant permission to operate. Check local requirements before use."}]
    return {**document, 'sections': context + document['sections']}


def build_package(actor, release_id: str, destination: Path) -> Path:
    # Serialize availability, rendering, and publication with controlled removal.
    with transaction():
        return _build_package(actor, release_id, destination)


def _build_package(actor, release_id: str, destination: Path) -> Path:
    release = get_release(actor, release_id)
    require_role(actor, release['program_id'], {'owner'})
    source = public_document(release)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f'onpf-{safe_id(release_id)}.zip'
    source_bytes = encode(source)
    if len(source_bytes) > 8 * 1024 * 1024 or len(source['materials']) > 100 or any(len(material['source_terms']) > 100 for material in source['materials']):
        raise DomainError('oversize_source', 'The public editable source exceeds the supported bounded import format.', 422)
    files = {'source/program.json': source_bytes, 'LICENSE': MIT_0_LICENSE.encode('utf-8')}
    for key in DOCUMENT_KEYS:
        document = display_document(source, key)
        files[f'documents/{key}.md'] = render_markdown(document).encode('utf-8')
        files[f'documents/{key}.odt'] = render_odt(document)
    files['tables/budget.csv'] = render_csv(source['documents']['budget']['rows'], list(ROW_FIELDS)).encode('utf-8')
    blank = blank_session(source)
    files['templates/session-plan.md'] = render_markdown(blank).encode('utf-8')
    files['templates/session-plan.odt'] = render_odt(blank)
    notices = ['# Notices and source terms', '', 'Project-owned framework and editable program documents are offered under the MIT No Attribution (MIT-0) license. Commercial use and proprietary adaptations are permitted. Contributing improvements back is encouraged, not required.', '', 'Selected third-party materials retain their recorded terms below; inclusion does not relicense them under MIT-0.', '']
    for material in source['materials']:
        files[f"materials/{safe_id(material['id'])}.json"] = encode(material)
        for label, term in [('Selected version', material)] + [('Inherited source version', item) for item in material['source_terms']]:
            notices.extend(['## ' + label + ': ' + literal(term['title']), ''])
            for key in ('id', 'version_number', 'ownership_basis', 'permission_basis', 'license', 'notices'):
                notices.extend([literal(key.replace('_', ' ').title()) + ': ' + literal(term[key]), ''])
    files['NOTICES.md'] = ('\n'.join(notices) + '\n').encode('utf-8')
    files['RELEASE-NOTES.md'] = ('# Release provenance\n\n' + '\n\n'.join(literal(f'{key}: {value}') for key, value in source['provenance'].items()) + '\n\nThe source snapshot hash identifies the private approved snapshot. It is not the checksum of this public projection. Editor change notes and internal rationale are not published.\n').encode('utf-8')
    files['README.md'] = b'''# Reusing this editable public package

Edit the Markdown, ODT, CSV, and source/program.json files locally. The source includes frozen document templates. templates/session-plan is a blank printable worksheet. Blank sections mark unresolved local requirements; estimated costs and unknown costs remain labeled. Check local permissions, accessibility, resources, and operating arrangements before use.

Project-owned content uses the MIT No Attribution (MIT-0) license: commercial use and proprietary adaptations are permitted, without requiring attribution for project-owned content. Improvements are welcome, never mandatory. Read NOTICES.md and materials/*.json for separately recorded original and adaptation terms.

An ONPF import validates checksums and creates a new unapproved draft with new identifiers. It imports no people, submissions, decisions, invitations, memberships, or approvals. Imported operating permission resets to pending and selected materials become unapproved local drafts requiring review.

Manifest SHA-256 hashes detect file corruption; they are not signatures or proof of ownership. The private source snapshot hash is provenance only. Public exports are not private backups.
'''
    manifest = {'format': FORMAT, 'schema_version': 1, 'framework_version': source['framework_version'], 'provenance': source['provenance'],
                'public_source_sha256': hashlib.sha256(source_bytes).hexdigest(), 'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    files['manifest.json'] = encode(manifest)
    if sum(len(value) for value in files.values()) > 100 * 1024 * 1024:
        raise DomainError('oversize_source', 'The public package expanded content exceeds 100 MiB.', 422)
    descriptor, temporary = tempfile.mkstemp(prefix='.onpf-', suffix='.tmp', dir=destination)
    os.close(descriptor)
    try:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        if Path(temporary).stat().st_size > 25 * 1024 * 1024:
            raise DomainError('oversize_archive', 'The generated public package exceeds the 25 MiB import limit.', 422)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output

"""Public package security and editable-source round trips using fictional data."""
import hashlib
import io
import json
import stat
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from xml.etree import ElementTree

import pytest

from onpf.errors import DomainError


def release(owner, program, text='Fictional approved café <script>& 🎨 text'):
    from onpf.programs.service import save_document
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    program = save_document(owner, program['id'], 'delivery', {'sections': {'activities': text}}, program['revision'])
    candidate = prepare_candidate(owner, program['id'], program['revision'], change_notes='PRIVATE_INTERNAL_NOTES')
    return get_release(owner, approve_candidate(owner, candidate['id'])['release_id'])


def package(owner, record, tmp_path):
    from onpf.exports.package import build_package
    return build_package(owner, record['id'], tmp_path)


def all_text(path):
    values = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            if name.endswith('.odt'):
                with ZipFile(io.BytesIO(data)) as odt:
                    for member in odt.namelist():
                        if member.endswith('.xml'):
                            xml = odt.read(member).decode('utf-8')
                            values.append(xml)
                            values.append(''.join(ElementTree.fromstring(xml).itertext()))
            else:
                values.append(data.decode('utf-8'))
    return '\n'.join(values)


def odf_text(element):
    namespace = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
    if element.tag == namespace + 's':
        return ' ' * int(element.get(namespace + 'c', '1'))
    if element.tag == namespace + 'tab':
        return '\t'
    if element.tag == namespace + 'line-break':
        return '\n'
    return (element.text or '') + ''.join(odf_text(child) + (child.tail or '') for child in element)


def mutate(path, change):
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    change(members)
    manifest = json.loads(members['manifest.json'])
    manifest['files'] = {name: hashlib.sha256(value).hexdigest() for name, value in members.items() if name != 'manifest.json'}
    manifest['public_source_sha256'] = hashlib.sha256(members['source/program.json']).hexdigest()
    members['manifest.json'] = json.dumps(manifest).encode()
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)


def test_public_package_excludes_private_sentinels(owner, program, tmp_path):
    from onpf.exports.projection import public_document
    record = release(owner, program)
    record.update(owner_labels={'x': 'PRIVATE_IDENTITY'}, responses=[{'text': 'PRIVATE_RAW', 'publication_permission': True}], decisions=[{'reason': 'PRIVATE_REASON'}], session_token='PRIVATE_TOKEN')
    public = json.dumps(public_document(record))
    assert all(marker not in public for marker in ['PRIVATE_IDENTITY', 'PRIVATE_RAW', 'PRIVATE_REASON', 'PRIVATE_TOKEN', 'PRIVATE_INTERNAL_NOTES', owner.user_id])
    output = all_text(package(owner, record, tmp_path))
    assert owner.user_id not in output and 'PRIVATE_INTERNAL_NOTES' not in output


def test_real_private_intake_and_credentials_never_exported(owner, program, batch, tmp_path):
    from onpf.auth.service import create_session, token_hash
    from onpf.inquiries.service import create_invitation
    from onpf.contributions.service import submit_responses
    from onpf.refinement.service import set_disposition
    from onpf.programs.service import get_program
    responses = submit_responses(owner, batch['id'], 'Fictional private submission', [{'question_id': batch['questions'][0]['id'], 'text': 'PRIVATE_REAL_RESPONSE_SENTINEL', 'attribution': 'alias', 'display_name': 'PRIVATE_REAL_ALIAS_SENTINEL', 'publication_permission': 'attributed_quote'}])
    for response_id in responses['response_ids']:
        set_disposition(owner, response_id, 'deferred', 'PRIVATE_DISPOSITION_RATIONALE_SENTINEL', [])
    session, invite = create_session(owner), create_invitation(owner, batch['id'])
    record = release(owner, get_program(owner, program['id']))
    output = all_text(package(owner, record, tmp_path))
    for marker in ['PRIVATE_REAL_RESPONSE_SENTINEL', 'PRIVATE_REAL_ALIAS_SENTINEL', 'PRIVATE_DISPOSITION_RATIONALE_SENTINEL', owner.user_id, session, invite, token_hash(session), token_hash(invite)]:
        assert marker not in output


def test_release_content_not_live_draft_exported(owner, program, tmp_path):
    from onpf.programs.service import save_document
    record = release(owner, program, 'FROZEN_APPROVED_WORDING')
    save_document(owner, program['id'], 'delivery', {'sections': {'activities': 'LATER_DRAFT_WORDING'}}, record['program_revision'])
    output = all_text(package(owner, record, tmp_path))
    assert 'FROZEN_APPROVED_WORDING' in output and 'LATER_DRAFT_WORDING' not in output


@pytest.mark.parametrize('value', ['=SUM(A1:A2)', '+1', '-1', '@A1', '\t=1', '\r=1', '\n=1', '   =1', '\u00a0+1'])
def test_csv_formula_neutralized(value):
    import csv
    from onpf.exports.csv import render_csv
    text = render_csv([{'item': value}], ['item'])
    assert list(csv.reader(io.StringIO(text)))[1][0] == "'" + value


def test_unicode_xml_and_safe_markdown(owner, program):
    from onpf.exports.odt import render_odt
    from onpf.exports.markdown import render_markdown
    from onpf.exports.projection import public_document
    text = 'Café <script>alert(1)</script> & "quoted" [link](javascript:evil) 🎨\nSecond line'
    document = public_document(release(owner, program, text))['documents']['delivery']
    with ZipFile(io.BytesIO(render_odt(document))) as archive:
        xml = archive.read('content.xml')
        assert '<script>' not in xml.decode()
        root = ElementTree.fromstring(xml)
        paragraphs = root.iter('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p')
        assert text in [odf_text(paragraph) for paragraph in paragraphs]
        assert archive.read('mimetype') == b'application/vnd.oasis.opendocument.text'
    markdown = render_markdown(document)
    assert '<script>' not in markdown and '[link](javascript:evil)' not in markdown


def test_manifest_hashes_versions_and_exact_mit(owner, program, tmp_path):
    record = release(owner, program)
    path = package(owner, record, tmp_path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['provenance']['source_snapshot_hash'] == record['content_hash']
        assert manifest['framework_version'] == record['framework']['version']
        assert set(manifest['files']) == set(archive.namelist()) - {'manifest.json'}
        for name, digest in manifest['files'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert archive.read('LICENSE').decode() == Path('LICENSE').read_text(encoding='utf-8')
        assert len([name for name in archive.namelist() if name.startswith('documents/') and name.endswith('.odt')]) == 7
        assert 'templates/session-plan.odt' in archive.namelist()
        assert len([name for name in archive.namelist() if name.startswith('documents/') and name.endswith('.md')]) == 7


def test_failed_export_retry_leaves_release_unchanged(owner, program, tmp_path, monkeypatch):
    from onpf.exports import package as module
    from onpf.releases.service import get_release
    record = release(owner, program)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'render_odt', lambda document: (_ for _ in ()).throw(OSError('fictional disk failure')))
        with pytest.raises(OSError):
            module.build_package(owner, record['id'], tmp_path)
    assert get_release(owner, record['id']) == record
    assert not list(tmp_path.glob('*.zip')) and not list(tmp_path.glob('*.tmp'))
    assert module.build_package(owner, record['id'], tmp_path).is_file()


def test_public_import_is_unapproved(owner, program, tmp_path):
    from onpf.exports.importer import import_program
    from onpf.db import get_db
    record = release(owner, program, 'Fictional imported text')
    imported = import_program(owner, package(owner, record, tmp_path), 'Fictional local adaptation')
    assert imported['id'] != program['id']
    assert imported['owner_ids'] == [owner.user_id] and imported['approval_rule'] == 'all'
    assert imported['operating_status'] == 'pending'
    assert imported['documents']['delivery']['sections']['activities'] == 'Fictional imported text'
    assert get_db().execute('SELECT count(*) FROM releases WHERE program_id=?', (imported['id'],)).fetchone()[0] == 0
    assert get_db().execute('SELECT source_json FROM imported_packages WHERE program_id=?', (imported['id'],)).fetchone()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/windows', 'safe/../../escape', 'safe\\evil', './odd'])
def test_zip_traversal_rejected(owner, program, tmp_path, name):
    from onpf.exports.importer import import_program
    path = package(owner, release(owner, program), tmp_path)
    mutate(path, lambda members: members.update({name: b'bad'}))
    with pytest.raises(DomainError, match='archive|package'):
        import_program(owner, path, 'Fictional unsafe import')


def test_symlink_rejected(owner, program, tmp_path):
    from onpf.exports.importer import import_program
    path = package(owner, release(owner, program), tmp_path)
    with ZipFile(path, 'a') as archive:
        info = ZipInfo('link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, 'target')
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional symlink import')


def test_oversize_archive_rejected(owner, tmp_path):
    from onpf.exports.importer import import_program
    path = tmp_path / 'oversize.zip'
    with path.open('wb') as stream:
        stream.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional oversized import')


def test_uncompressed_archive_rejected(owner, program, tmp_path):
    from onpf.exports.importer import import_program
    path = package(owner, release(owner, program), tmp_path)
    with ZipFile(path, 'a', ZIP_DEFLATED) as archive:
        archive.writestr('bomb.txt', b'x' * (100 * 1024 * 1024 + 1))
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional oversized content')


def test_hash_failure_and_bad_source_make_no_database_changes(owner, program, tmp_path):
    from onpf.exports.importer import import_program
    from onpf.db import get_db
    path = package(owner, release(owner, program), tmp_path)
    count = get_db().execute('SELECT count(*) FROM programs').fetchone()[0]
    def bad(members):
        source = json.loads(members['source/program.json'])
        source['owner_ids'] = [owner.user_id]
        members['source/program.json'] = json.dumps(source).encode()
    mutate(path, bad)
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional authority injection')
    assert get_db().execute('SELECT count(*) FROM programs').fetchone()[0] == count
    with ZipFile(path, 'a') as archive:
        archive.writestr('tamper.txt', 'tampered')
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional hash failure')
    assert get_db().execute('SELECT count(*) FROM programs').fetchone()[0] == count


def test_material_terms_and_draft_import(owner, program, tmp_path):
    from onpf.materials.service import save_material, release_material, adopt_material, list_materials, list_adoptions
    from onpf.programs.service import get_program
    from onpf.exports.importer import import_program
    material = save_material(owner, program['id'], {'title': 'Fictional licensed source', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional copied material'}, 'ownership_basis': 'third_party', 'permission_basis': 'Fictional recorded permission', 'license': 'Fictional proprietary reuse terms', 'notices': 'Fictional retain this notice'}, None)
    version = release_material(owner, material['id'], material['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    record = release(owner, get_program(owner, program['id']))
    path = package(owner, record, tmp_path)
    text = all_text(path)
    assert 'Fictional proprietary reuse terms' in text and 'Fictional retain this notice' in text
    imported = import_program(owner, path, 'Fictional imported material program')
    drafts = list_materials(owner, imported['id'])
    assert len(drafts) == 1 and drafts[0]['id'] != material['id']
    assert drafts[0]['license'] == version['license'] and drafts[0]['notices'] == version['notices']
    assert list_adoptions(owner, imported['id']) == []
    assert drafts[0]['source_terms'][0]['id'] == version['id']
    from onpf.materials.service import get_version, derive_material
    republished = release_material(owner, drafts[0]['id'], drafts[0]['revision'])
    assert republished['source_terms'][0]['license'] == version['license']
    derived = derive_material(owner, imported['id'], republished['id'])
    assert derived['source_terms'][1]['id'] == version['id']
    with pytest.raises(DomainError):
        save_material(owner, imported['id'], {**{key: drafts[0][key] for key in ('id', 'title', 'content', 'ownership_basis', 'permission_basis', 'license')}, 'notices': 'Fictional erased notices'}, drafts[0]['revision'])


def test_exports_owner_authority(owner, facilitator, program, tmp_path):
    from onpf.exports.package import build_package
    from onpf.exports.importer import import_program
    record = release(owner, program)
    with pytest.raises(DomainError) as denied:
        build_package(facilitator, record['id'], tmp_path)
    assert denied.value.status == 403
    imported = import_program(facilitator, package(owner, record, tmp_path), 'Fictional new independently owned program')
    assert imported['owner_ids'] == [facilitator.user_id]


def test_browser_export_import_print(login_client, owner, program, tmp_path, csrf_token):
    record = release(owner, program, '<script>Fictional browser literal</script>')
    page = login_client.get('/releases/' + record['id'])
    assert '/exports/releases/' + record['id'] in page.text
    printed = login_client.get('/exports/releases/' + record['id'] + '/print')
    assert printed.status_code == 200 and '&lt;script&gt;' in printed.text and 'PRIVATE_INTERNAL_NOTES' not in printed.text
    token = csrf_token(login_client, '/releases/' + record['id'])
    exported = login_client.post('/exports/releases/' + record['id'] + '/package', data={'csrf_token': token})
    assert exported.status_code == 200 and exported.mimetype == 'application/zip'
    token = csrf_token(login_client, '/exports/import')
    imported = login_client.post('/exports/import', data={'csrf_token': token, 'title': 'Fictional browser adoption', 'archive': (io.BytesIO(exported.data), 'program.zip')})
    assert imported.status_code == 302
    assert login_client.post('/exports/import', data={'title': 'No CSRF'}).status_code == 400


def test_corrupt_checksum_rejected_without_writes(owner, program, tmp_path):
    from onpf.exports.importer import import_program
    from onpf.db import get_db
    path = package(owner, release(owner, program), tmp_path)
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members['documents/delivery.md'] = b'Tampered bytes'
    with ZipFile(path, 'w') as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    count = get_db().execute('SELECT count(*) FROM programs').fetchone()[0]
    with pytest.raises(DomainError):
        import_program(owner, path, 'Fictional corrupt import')
    assert get_db().execute('SELECT count(*) FROM programs').fetchone()[0] == count


@pytest.mark.parametrize('payload', [b'[' * 33 + b'0' + b']' * 33, b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff'])
def test_bounded_json_rejects_hostile_input(payload):
    from onpf.exports.importer import bounded_json
    with pytest.raises(DomainError):
        bounded_json(payload)


def test_unknown_document_keys_rejected(owner, program):
    from onpf.exports.projection import public_document
    record = release(owner, program)
    record['documents']['unsupported'] = {'sections': {}}
    with pytest.raises(DomainError, match='seven supported'):
        public_document(record)


def test_import_in_separate_blank_instance(owner, program, tmp_path):
    from onpf.app import create_app
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    from onpf.exports.importer import import_program
    from onpf.db import get_db
    record = release(owner, program)
    path = package(owner, record, tmp_path)
    blank = create_app({'TESTING': True, 'DATABASE': str(tmp_path / 'separate.sqlite3'), 'INSTANCE_PATH': str(tmp_path / 'separate-instance')})
    with blank.app_context():
        new_owner = Principal(create_user('Fictional adopter', 'fictional-adopter-password'), None, None)
        imported = import_program(new_owner, path, 'Fictional separate adoption')
        assert imported['owner_ids'] == [new_owner.user_id] and imported['operating_status'] == 'pending'
        assert get_db().execute('SELECT count(*) FROM release_candidates').fetchone()[0] == 0
        assert get_db().execute('SELECT count(*) FROM responses').fetchone()[0] == 0
        source = json.loads(get_db().execute('SELECT source_json FROM imported_packages').fetchone()[0])
        assert source['provenance']['release_id'] == record['id']


def test_failed_import_rolls_back_new_program(owner, program, tmp_path, monkeypatch):
    from onpf.exports import importer
    from onpf.db import get_db
    path = package(owner, release(owner, program), tmp_path)
    count = get_db().execute('SELECT count(*) FROM programs').fetchone()[0]
    monkeypatch.setattr(importer, 'save_document', lambda *args: (_ for _ in ()).throw(DomainError('failure', 'Fictional write failure', 422)))
    with pytest.raises(DomainError):
        importer.import_program(owner, path, 'Fictional atomic failure')
    assert get_db().execute('SELECT count(*) FROM programs').fetchone()[0] == count
    assert get_db().execute('SELECT count(*) FROM imported_packages').fetchone()[0] == 0


def test_unsupported_xml_characters_are_rejected():
    from onpf.exports.odt import render_odt
    with pytest.raises(DomainError):
        render_odt({'title': 'Fictional invalid character', 'sections': [{'key': 'text', 'label': 'Text', 'text': '\x01'}]})


def test_withdrawn_release_projection_unavailable(owner, program):
    from onpf.exports.projection import public_document
    record = release(owner, program)
    record['state'] = 'withdrawn'
    with pytest.raises(DomainError) as error:
        public_document(record)
    assert error.value.status == 409


def test_print_preserves_shared_document_budget_rows(login_client, owner, program):
    from onpf.materials.service import save_material, release_material, adopt_material
    from onpf.programs.service import get_program
    row = {'item': 'Fictional shared <paint>', 'quantity': '2 <kits>', 'unit_cost': '19.25',
           'cost_status': 'estimated', 'notes': 'Fictional shared <script>literal note</script> & local review'}
    material = save_material(owner, program['id'], {'title': 'Fictional shared budget bundle',
        'content': {'schema_version': 1, 'kind': 'document_bundle', 'documents': {
            'shared-budget': {'title': 'Fictional shared resource costs', 'sections': [], 'rows': [row]}}},
        'ownership_basis': 'own_work', 'permission_basis': 'Fictional owner permission',
        'license': 'MIT', 'notices': 'Fictional shared notice'}, None)
    version = release_material(owner, material['id'], material['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    record = release(owner, get_program(owner, program['id']))
    response = login_client.get('/exports/releases/' + record['id'] + '/print')
    assert response.status_code == 200
    for value in ['Fictional shared &lt;paint&gt;', '2 &lt;kits&gt;', '19.25', 'estimated',
                  'Fictional shared &lt;script&gt;literal note&lt;/script&gt; &amp; local review']:
        assert f'<td class="preserved">{value}</td>' in response.text
    assert '<script>literal note</script>' not in response.text

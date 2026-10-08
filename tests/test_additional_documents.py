"""Program media is editable in private and enters only reviewed public versions."""
import io
from zipfile import ZipFile

import pytest

from onpf.errors import DomainError


def payload(template='recipe-card', *, selected=False):
    return {'template_key': template, 'title': 'Fictional lentil recipe',
            'sections': {'yield': 'Four portions', 'ingredients': 'Lentils and water',
                         'method': 'Simmer until tender', 'safety': 'Check temperature',
                         'notes': 'Use available pantry supplies'},
            'ownership_basis': 'own_work', 'license': 'MIT-0',
            'permission_basis': 'Created by this project', 'notices': '',
            'include_in_public': selected}


def test_template_media_create_edit_and_stale_revision(owner, program):
    from onpf.additional_documents.service import get_additional, save_additional
    from onpf.programs.service import get_program

    first = save_additional(owner, program['id'], payload(), program['revision'])
    assert first['sections']['ingredients'] == 'Lentils and water'
    assert first['include_in_public'] is False
    current = get_program(owner, program['id'])
    edited = payload()
    edited['sections']['ingredients'] = 'Lentils, water, carrots'
    second = save_additional(owner, program['id'], edited, current['revision'], first['id'])
    assert get_additional(owner, program['id'], first['id'])['sections']['ingredients'] == 'Lentils, water, carrots'
    assert second['id'] == first['id']
    with pytest.raises(DomainError) as error:
        save_additional(owner, program['id'], edited, current['revision'], first['id'])
    assert error.value.code == 'stale_revision'


def test_upload_selection_and_frozen_public_download(owner, program, app):
    from onpf.additional_documents.service import save_additional
    from onpf.programs.service import get_program
    from onpf.publications.service import get_publication, publish

    private = save_additional(owner, program['id'], payload(), program['revision'],
                              upload=b'%PDF-1.7 private', filename='private.pdf')
    current = get_program(owner, program['id'])
    poster = {'template_key': 'pantry-poster', 'title': 'Pantry wall poster',
              'sections': {'heading': 'Shared pantry', 'supplies': 'Cups and spoons',
                           'use': 'Take what you need', 'storage': 'Keep dry',
                           'replenishment': 'Tell the coordinator'},
              'ownership_basis': 'own_work', 'license': 'MIT-0',
              'permission_basis': 'Created by this project', 'notices': '',
              'include_in_public': True}
    selected = save_additional(owner, program['id'], poster, current['revision'],
                               upload=b'%PDF-1.7 public poster', filename='poster.pdf')
    current = get_program(owner, program['id'])
    publish(owner, program['id'], current['revision'], reviewed=True, selected_additional_ids=[selected['id']])
    source = get_publication(program['id'], 1)
    assert [item['id'] for item in source['additional_documents']] == [selected['id']]
    root = f"/projects/{program['id']}/versions/1/additional/{selected['id']}"
    browser = app.test_client()
    assert browser.get(root + '.html').status_code == 200
    assert browser.get(root + '.odt').status_code == 200
    assert browser.get(root + '/attachment').data == b'%PDF-1.7 public poster'
    assert browser.get(f"/projects/{program['id']}/versions/1/additional/{private['id']}/attachment").status_code == 404
    with ZipFile(io.BytesIO(browser.get(f"/projects/{program['id']}/versions/1/package.zip").data)) as bundle:
        assert f'additional/{selected["id"]}.odt' in bundle.namelist()
        assert f'additional/attachments/{selected["id"]}.pdf' in bundle.namelist()
        assert not any(private['id'] in name for name in bundle.namelist())
    updated = dict(poster)
    updated['sections'] = {**poster['sections'], 'supplies': 'New list'}
    current = get_program(owner, program['id'])
    save_additional(owner, program['id'], updated, current['revision'], selected['id'])
    assert get_publication(program['id'], 1)['additional_documents'][0]['sections'][1]['text'] == 'Cups and spoons'


def test_upload_validation_and_viewer_authorization(owner, program, app_context):
    from onpf.additional_documents.service import save_additional
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    from onpf.db import get_db

    viewer_id = create_user('media-viewer', 'fictional-viewer-password')
    get_db().execute('INSERT INTO memberships(program_id,user_id,role) VALUES (?,?,?)', (program['id'], viewer_id, 'viewer'))
    with pytest.raises(DomainError) as error:
        save_additional(Principal(viewer_id, None, None), program['id'], payload(), program['revision'])
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        save_additional(owner, program['id'], payload(), program['revision'], upload=b'<script>x</script>', filename='poster.svg')
    assert error.value.status == 422
    with pytest.raises(DomainError) as error:
        save_additional(owner, program['id'], payload(), program['revision'], upload=b'%PDF-1.7' + b'x' * (5 * 1024 * 1024), filename='large.pdf')
    assert error.value.status == 422


def test_additional_document_editor_and_private_download(owner, program, login_client, csrf_token):
    from onpf.additional_documents.service import save_additional
    entry = save_additional(owner, program['id'], payload(), program['revision'])
    page = login_client.get(f"/programs/{program['id']}/additional/{entry['id']}")
    assert page.status_code == 200
    assert b'Lentils and water' in page.data
    assert login_client.get(f"/programs/{program['id']}/additional/{entry['id']}.html").status_code == 200
    assert login_client.get(f"/programs/{program['id']}/additional/{entry['id']}.odt").status_code == 200


def test_browser_form_creates_poster_with_attachment(owner, program, login_client, csrf_token):
    from onpf.additional_documents.service import list_additional
    from onpf.programs.service import get_program

    path = f"/programs/{program['id']}/additional?template=pantry-poster"
    token = csrf_token(login_client, f"/programs/{program['id']}/additional/new")
    assert b'Create supporting material or reusable template' in login_client.get(path).data
    form = {'csrf_token': token, 'expected_revision': str(get_program(owner, program['id'])['revision']),
            'template_key': 'pantry-poster', 'title': 'Kitchen supply wall poster',
            'section_heading': 'Kitchen supplies', 'section_supplies': 'Spoons and cups',
            'section_use': 'Return after use', 'section_storage': 'Dry shelf',
            'section_replenishment': 'Tell the coordinator',
            'ownership_basis': 'own_work', 'permission_basis': 'Created by this project',
            'license': 'MIT-0', 'notices': '',
            'attachment': (io.BytesIO(b'%PDF-1.7 poster'), 'poster.pdf')}
    response = login_client.post(f"/programs/{program['id']}/additional", data=form, content_type='multipart/form-data')
    assert response.status_code == 302
    item = list_additional(owner, program['id'])[0]
    assert item['title'] == 'Kitchen supply wall poster'
    assert item['include_in_public'] is False
    assert login_client.get(f"/programs/{program['id']}/additional/{item['id']}/attachment").data == b'%PDF-1.7 poster'


def test_third_party_media_requires_notice_and_private_backup_roundtrip(owner, program, app, tmp_path):
    from onpf.additional_documents.service import get_additional, save_additional
    from onpf.archives.service import backup_private, restore_private
    from onpf.app import create_app
    from onpf.publications.service import get_publication, publish
    from onpf.programs.service import get_program

    third_party = payload()
    third_party['ownership_basis'] = 'third_party'
    third_party['license'] = 'Permission for this project only'
    with pytest.raises(DomainError) as error:
        save_additional(owner, program['id'], third_party, program['revision'])
    assert error.value.code == 'missing_notices'
    third_party['notices'] = 'Original creator retained rights.'
    third_party['include_in_public'] = True
    item = save_additional(owner, program['id'], third_party, program['revision'],
                           upload=b'%PDF-1.7 source', filename='recipe.pdf')
    publish(owner, program['id'], get_program(owner, program['id'])['revision'], reviewed=True, selected_additional_ids=[item['id']])
    archive = tmp_path / 'PRIVATE.json'
    backup_private(app.config['DATABASE'], archive)
    restored_path = tmp_path / 'restored.sqlite3'
    restore_private(archive, restored_path)
    restored = create_app({'TESTING': True, 'DATABASE': str(restored_path),
                           'INSTANCE_PATH': str(tmp_path / 'restored-instance')})
    with restored.app_context():
        assert get_additional(owner, program['id'], item['id'])['upload_sha256'] == item['upload_sha256']
        assert get_publication(program['id'], 1)['additional_documents'][0]['notices'] == 'Original creator retained rights.'


def test_attached_odt_keeps_generated_odt_in_separate_package_path(owner, program, app):
    from onpf.additional_documents.service import save_additional
    from onpf.exports.odt import render_odt
    from onpf.programs.service import get_program
    from onpf.publications.service import publish

    original = render_odt({'title': 'Uploaded source', 'sections': [{'key': 'body', 'label': 'Body', 'text': 'Original file'}]})
    entry = save_additional(owner, program['id'], payload(selected=True), program['revision'],
                            upload=original, filename='source.odt')
    publish(owner, program['id'], get_program(owner, program['id'])['revision'], reviewed=True, selected_additional_ids=[entry['id']])
    package = app.test_client().get(f"/projects/{program['id']}/versions/1/package.zip").data
    with ZipFile(io.BytesIO(package)) as archive:
        assert archive.read(f"additional/attachments/{entry['id']}.odt") == original
        assert archive.read(f"additional/{entry['id']}.odt") != original


def test_offered_media_requires_owner_selection(owner, program, login_client, csrf_token):
    from onpf.additional_documents.service import save_additional
    from onpf.programs.service import get_program
    from onpf.publications.service import get_publication, publish

    item = save_additional(owner, program['id'], payload(selected=True), program['revision'])
    path = f"/programs/{program['id']}/publish"
    page = login_client.get(path)
    assert page.status_code == 200
    assert f'value="{item["id"]}"' in page.text
    assert 'Include this item in this public version' in page.text
    revision = get_program(owner, program['id'])['revision']
    publish(owner, program['id'], revision, reviewed=True)
    assert get_publication(program['id'], 1)['additional_documents'] == []
    with pytest.raises(DomainError) as error:
        publish(owner, program['id'], revision, reviewed=True, selected_additional_ids=['missing'])
    assert error.value.code == 'invalid_selection'
    token = csrf_token(login_client, path)
    posted = login_client.post(path, data={'csrf_token': token, 'expected_revision': str(revision),
        'reviewed': 'yes', 'additional_ids': item['id']})
    assert posted.status_code == 302
    assert [entry['id'] for entry in get_publication(program['id'], 2)['additional_documents']] == [item['id']]

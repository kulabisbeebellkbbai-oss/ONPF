"""Fictional end-to-end evidence, including real CSRF-protected form writes."""
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest

from onpf.auth.models import Principal
from onpf.auth.service import authenticate
from onpf.cli import main
from onpf.contributions.service import get_response, list_responses, revise_response
from onpf.db import get_db
from onpf.programs.service import create_program, get_program, save_document


def archive_text(path):
    parts = []
    with ZipFile(path) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            if name.endswith('.odt'):
                with ZipFile(io.BytesIO(data)) as odt:
                    for member in odt.namelist():
                        if member.endswith('.xml'):
                            xml = odt.read(member).decode()
                            parts.extend([xml, ''.join(ElementTree.fromstring(xml).itertext())])
            else:
                parts.append(data.decode())
    return '\n'.join(parts)


def test_two_program_round_trip(app, login_client, owner, csrf_token, tmp_path):
    from onpf.inquiries.service import issue_batch, create_invitation
    from onpf.refinement.service import save_proposal, set_disposition, record_decision
    from onpf.releases.service import prepare_candidate, approve_candidate, get_release
    from onpf.materials.service import material_from_release, adopt_material, save_material, release_material
    from onpf.exports.package import build_package
    from onpf.exports.importer import import_program
    from onpf.archives.service import backup_private, restore_private
    from onpf.app import create_app

    art = create_program(owner, {'title': 'FICTIONAL guided art', 'module_keys': ['art']})
    broader = create_program(owner, {'title': 'FICTIONAL creative and growing', 'module_keys': ['art', 'gardening', 'hobby', 'maker']})
    batch = issue_batch(owner, art['id'], {'title': 'FICTIONAL perspectives', 'question_ids': ['core-1-1', 'core-4-3']})
    invitation = create_invitation(owner, batch['id'])
    guest = app.test_client()
    token = csrf_token(guest, '/invitations/answer')
    assert guest.post('/invitations/accept', data={'token': invitation, 'csrf_token': token}).status_code == 302
    fields = {'row_id': ['0', '1', '2'], 'submission_key': 'FICTIONAL_PRIVATE_RETRY', 'csrf_token': csrf_token(guest, '/invitations/answer')}
    for index, (attribution, state) in enumerate([('named', 'answered'), ('alias', 'answered'), ('anonymous', 'abstain')]):
        fields.update({f'question_id_{index}': batch['questions'][0]['id'], f'text_{index}': '' if state == 'abstain' else f'PRIVATE_PERSPECTIVE_{index}', f'attribution_{index}': attribution, f'display_name_{index}': '' if attribution == 'anonymous' else f'PRIVATE_PERSON_{index}', f'answer_state_{index}': state})
    assert guest.post('/invitations/contributions', data=fields).status_code == 302
    responses = list_responses(owner, art['id'])
    path = f"/responses/{responses[0]['id']}/revise"
    assert login_client.post(path, data={'csrf_token': csrf_token(login_client, path[:-7]), 'expected_revision': 1, 'text': 'PRIVATE_CORRECTION', 'reason': 'PRIVATE_REQUEST'}).status_code == 302
    assert [row['text'] for row in get_response(owner, responses[0]['id'])['history']] == ['PRIVATE_PERSPECTIVE_0', 'PRIVATE_CORRECTION']
    proposal = save_proposal(owner, art['id'], {'title': 'FICTIONAL quiet choice', 'text': 'Offer a quiet activity choice', 'response_ids': [responses[0]['id']]}, None)
    for response, status in zip(responses, ['incorporated', 'declined', 'deferred']):
        set_disposition(owner, response['id'], status, 'PRIVATE_RATIONALE', [proposal['id']] if status == 'incorporated' else [])
    decision = record_decision(owner, art['id'], {'outcome': 'Offer quiet optional art', 'rationale': 'PRIVATE_DECISION_REASON', 'proposal_ids': [proposal['id']], 'response_ids': [responses[0]['id']]})
    art = save_document(owner, art['id'], 'delivery', {'sections': {'activities': 'FICTIONAL optional guided art'}, 'decision_ids': [decision['id']]}, get_program(owner, art['id'])['revision'])
    candidate = prepare_candidate(owner, art['id'], art['revision'], change_notes='PRIVATE_APPROVAL_NOTE')
    art_release = get_release(owner, approve_candidate(owner, candidate['id'])['release_id'])
    version = material_from_release(owner, art_release['id'])
    adopt_material(owner, broader['id'], version['id'], broader['revision'])
    candidate = prepare_candidate(owner, broader['id'], get_program(owner, broader['id'])['revision'], permission_reviewed=True)
    broader_release = get_release(owner, approve_candidate(owner, candidate['id'], permission_reviewed=True)['release_id'])
    assert art_release['id'] != broader_release['id']
    public_packages = [build_package(owner, release['id'], tmp_path / 'public') for release in [art_release, broader_release]]
    private_markers = ['PRIVATE_PERSPECTIVE_0', 'PRIVATE_PERSPECTIVE_1', 'PRIVATE_PERSON_0', 'PRIVATE_PERSON_1', 'PRIVATE_CORRECTION', 'PRIVATE_REQUEST', 'PRIVATE_RATIONALE', 'PRIVATE_DECISION_REASON', 'PRIVATE_APPROVAL_NOTE', 'FICTIONAL_PRIVATE_RETRY', invitation, owner.user_id]
    for path in public_packages:
        assert all(marker not in archive_text(path) for marker in private_markers)
        with ZipFile(path) as archive:
            assert len([name for name in archive.namelist() if name.startswith('documents/') and name.endswith('.odt')]) == 7
            assert 'source/program.json' in archive.namelist()
    adaptation = import_program(owner, public_packages[1], 'FICTIONAL agency adaptation')
    assert adaptation['operating_status'] == 'pending'
    broader_hash_before_art_edit = broader_release['content_hash']
    material = save_material(owner, art['id'], {'id': version['material_id'], 'title': 'FICTIONAL later guide', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'FICTIONAL later material'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional authors own this example', 'license': 'MIT', 'notices': 'Fictional example'}, 1)
    release_material(owner, material['id'], material['revision'])
    broader_hash_after_art_edit = get_release(owner, broader_release['id'])['content_hash']
    assert broader_hash_after_art_edit == broader_hash_before_art_edit
    original_release_hashes = {release['id']: release['content_hash'] for release in [art_release, broader_release]}
    backup = backup_private(Path(app.config['DATABASE']), tmp_path / 'private.json')
    restore_private(backup, tmp_path / 'restored.sqlite3')
    restored = create_app({'INSTANCE_PATH': str(tmp_path / 'restored-instance'), 'DATABASE': str(tmp_path / 'restored.sqlite3'), 'TESTING': True})
    with restored.app_context():
        restored_owner = authenticate('owner', 'fictional-owner-password')
        restored_release_hashes = {identifier: get_release(restored_owner, identifier)['content_hash'] for identifier in original_release_hashes}
        assert restored_release_hashes == original_release_hashes
        assert len(get_response(restored_owner, responses[0]['id'])['history']) == 2
        assert get_program(restored_owner, adaptation['id'])['title'] == 'FICTIONAL agency adaptation'
        for identifier in original_release_hashes:
            assert all(marker not in archive_text(build_package(restored_owner, identifier, tmp_path / 'restored-public')) for marker in private_markers)


def test_malformed_login_destination_falls_back_without_orphan_session(client, owner, csrf_token):
    response = client.post('/login?next=http://[', data={'username': 'owner', 'password': 'fictional-owner-password', 'csrf_token': csrf_token(client)})
    assert response.status_code == 302 and response.location == '/workspace'
    assert client.get('/workspace').status_code == 200
    assert get_db().execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 1


@pytest.mark.parametrize('role', ['', 'contributor'])
def test_self_membership_change_redirects_to_accessible_index(login_client, owner, other_owner, program, csrf_token, role):
    path = f"/programs/{program['id']}/settings"
    result = login_client.post(path, data={'csrf_token': csrf_token(login_client, path), 'expected_revision': program['revision'], 'title': program['title'], 'operating_status': 'pending', 'approval_rule': 'all', 'membership_editor': 'yes', f'role_{owner.user_id}': role, f'role_{other_owner.user_id}': 'owner'})
    assert result.status_code == 302 and result.location == '/programs'
    assert login_client.get(result.location).status_code == 200


def test_stale_correction_requires_explicit_acknowledgement(login_client, owner, submitted, csrf_token):
    identifier = submitted['response_ids'][0]
    path = f'/responses/{identifier}'
    token = csrf_token(login_client, path)
    revise_response(owner, identifier, 'Fictional concurrent correction', 'Fictional other request', 1)
    payload = {'csrf_token': token, 'expected_revision': 1, 'text': 'Fictional retained draft', 'reason': 'Fictional retained reason'}
    result = login_client.post(path + '/revise', data=payload)
    assert result.status_code == 409 and 'Fictional retained draft' in result.text
    assert 'name="acknowledge_revision"' in result.text
    assert 'name="expected_revision" value="2"' in result.text
    assert 'Fictional concurrent correction' in result.text
    # Removing the required acknowledgement must not overwrite the current response.
    payload.update(expected_revision=2, conflict_revision=2)
    assert login_client.post(path + '/revise', data=payload).status_code == 422
    assert get_response(owner, identifier)['revision'] == 2
    payload['acknowledge_revision'] = '2'
    assert login_client.post(path + '/revise', data=payload).status_code == 302
    assert get_response(owner, identifier)['revision'] == 3


def test_material_comparison_uses_reader_labels(owner, program):
    from onpf.materials.service import save_material, release_material, compare_versions
    payload = {'title': 'Fictional guide', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional before'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional authors', 'license': 'MIT', 'notices': 'Fictional notice'}
    draft = save_material(owner, program['id'], payload, None)
    left = release_material(owner, draft['id'], 1)
    payload.update(id=draft['id'], permission_basis='Fictional updated permission', content={'schema_version': 1, 'kind': 'text', 'text': 'Fictional after'})
    draft = save_material(owner, program['id'], payload, 1)
    right = release_material(owner, draft['id'], draft['revision'])
    diff = compare_versions(owner, left['id'], right['id'])['diff']
    assert 'Permission basis:' in diff and 'Material text:' in diff
    assert 'schema_version' not in diff and 'permission_basis' not in diff
    assert 'Earlier: Fictional before' in diff and 'Later: Fictional after' in diff
    assert '@@' not in diff and '--- Version' not in diff


def test_demo_creates_separate_fictional_database_and_refuses_existing(tmp_path, capsys):
    path = tmp_path / 'demo' / 'onpf.sqlite3'
    assert main(['demo', '--database', str(path)]) == 0
    output = capsys.readouterr().out
    assert 'FICTIONAL' in output and 'fictional-owner' in output
    original = path.read_bytes()
    assert main(['demo', '--database', str(path)]) == 1
    assert path.read_bytes() == original


def test_readiness_identifies_instance_without_private_paths(app, client):
    result = client.get('/health')
    assert result.status_code == 200
    assert result.json == {'application': 'onpf', 'version': '0.3.0', 'instance_id': app.config['INSTANCE_ID']}
    assert app.instance_path not in result.text


@pytest.mark.parametrize('existing_target', [False, True])
def test_demo_refuses_supplied_symlink_without_following_target(tmp_path, existing_target):
    from onpf.demo import create_demo
    target = tmp_path / 'target.sqlite3'
    if existing_target:
        target.write_bytes(b'Fictional existing destination must remain untouched')
    link = tmp_path / 'supplied-link.sqlite3'
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f'Native symlink creation unavailable: {error}')
    with pytest.raises(FileExistsError):
        create_demo(link)
    assert link.is_symlink()
    assert not (tmp_path / 'target-instance').exists()
    if existing_target:
        assert target.read_bytes() == b'Fictional existing destination must remain untouched'
    else:
        assert not target.exists()

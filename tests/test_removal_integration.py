"""Final-review regressions using fictional data through real services, HTTP and CLI."""
import io
import json
from pathlib import Path

import pytest

from onpf.archives.service import redact_response
from onpf.db import connect, get_db
from onpf.errors import DomainError
from onpf.materials import service as materials
from onpf.programs.service import create_program, get_program, save_document, update_program
from onpf.refinement.service import create_review_draft, read_review_draft, record_decision


SENTINEL = 'Fictional first perspective'


def material_payload(text=SENTINEL, **changes):
    return {'title': SENTINEL, 'content': {'schema_version': 1, 'kind': 'text', 'text': text},
            'ownership_basis': 'own_work', 'permission_basis': SENTINEL, 'license': 'MIT',
            'notices': SENTINEL, **changes}


def affected_selection(owner, program, other_owner, adopter):
    draft = materials.save_material(owner, program['id'], material_payload(), None)
    version = materials.release_material(owner, draft['id'], draft['revision'])
    materials.adopt_material(other_owner, adopter['id'], version['id'], get_program(other_owner, adopter['id'])['revision'])
    return draft, version


def clean_replacement(owner, program, draft):
    clean = materials.save_material(owner, program['id'], material_payload('Fictional clean replacement', id=draft['id'], title='Fictional clean guide', permission_basis='Own authorship', notices='Copyright fictional authors'), draft['revision'])
    return materials.release_material(owner, clean['id'], clean['revision'])


def other_login(client, csrf_token):
    assert client.post('/login', data={'username': 'other-owner', 'password': 'fictional-other-password', 'csrf_token': csrf_token(client)}).status_code == 302


def test_removed_shared_library_hides_content_and_terms_but_keeps_private_draft(owner, other_owner, program, submitted, client, csrf_token):
    adopter = create_program(other_owner, {'title': 'Fictional separate agency'})
    draft, version = affected_selection(owner, program, other_owner, adopter)
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert SENTINEL not in json.dumps(materials.list_versions(other_owner))
    assert materials.list_versions(other_owner, draft['id']) == []
    with pytest.raises(DomainError, match='unavailable'):
        materials.get_version(other_owner, version['id'])
    assert SENTINEL in json.dumps(materials.get_material(owner, program['id'], draft['id']))
    other_login(client, csrf_token)
    page = client.get(f"/programs/{adopter['id']}/materials")
    assert page.status_code == 200
    assert SENTINEL not in page.text
    assert 'Unavailable selected version' in page.text
    assert 'Remove selection' in page.text
    assert 'Create a material draft' in page.text
    replacement = clean_replacement(owner, program, draft)
    path = f"/programs/{adopter['id']}/materials"
    response = client.post(path + '/adopt', data={'csrf_token': csrf_token(client, path), 'version_id': replacement['id'], 'expected_revision': get_program(other_owner, adopter['id'])['revision']}, follow_redirects=True)
    assert response.status_code == 200 and 'Unavailable selected version' not in response.text
    assert SENTINEL not in response.text
    assert materials.list_adoptions(other_owner, adopter['id'])[0]['version_id'] == replacement['id']


def test_inherited_quarantined_terms_are_not_a_shared_preview(owner, other_owner, program, submitted):
    adopter = create_program(other_owner, {'title': 'Fictional separate agency'})
    draft = materials.save_material(owner, program['id'], material_payload('Fictional original', title='Fictional source', notices='Copyright fictional source'), None)
    source = materials.release_material(owner, draft['id'], draft['revision'])
    derivative = materials.derive_material(other_owner, adopter['id'], source['id'])
    # A source permission basis is inherited without being copied into the local basis.
    derivative = materials.save_material(other_owner, adopter['id'], material_payload('Fictional distinct derivative', id=derivative['id'], title='Fictional derivative', permission_basis='Own adaptation', notices='Copyright fictional source'), derivative['revision'])
    version = materials.release_material(other_owner, derivative['id'], derivative['revision'])
    materials.adopt_material(other_owner, adopter['id'], version['id'], get_program(other_owner, adopter['id'])['revision'])
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    assert SENTINEL not in json.dumps(materials.list_versions(other_owner))
    with pytest.raises(DomainError):
        materials.get_version(other_owner, version['id'])
    assert materials.list_adoptions(other_owner, adopter['id'])[0]['version'] is None
    from onpf.releases.service import prepare_candidate
    with pytest.raises(DomainError) as denied:
        prepare_candidate(other_owner, adopter['id'], get_program(other_owner, adopter['id'])['revision'])
    assert denied.value.code == 'quarantined_content'


def test_two_unavailable_selections_can_be_replaced_sequentially(owner, other_owner, program, submitted):
    adopter = create_program(other_owner, {'title': 'Fictional separate agency'})
    selections = [affected_selection(owner, program, other_owner, adopter) for _ in range(2)]
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    replacements = [clean_replacement(owner, program, draft) for draft, _ in selections]
    start = get_program(other_owner, adopter['id'])['revision']
    for index, version in enumerate(replacements):
        changed = materials.adopt_material(other_owner, adopter['id'], version['id'], start + index)
        assert changed['version_id'] == version['id']
        current = materials.list_adoptions(other_owner, adopter['id'])
        assert sum(item['available'] for item in current) == index + 1
    assert get_program(other_owner, adopter['id'])['revision'] == start + 2


def test_remove_unavailable_selection_requires_owner_current_revision_and_keeps_frozen_history(owner, other_owner, facilitator, program, submitted, client, csrf_token):
    from onpf.releases.service import prepare_candidate, approve_candidate
    adopter = create_program(other_owner, {'title': 'Fictional separate agency', 'memberships': [{'user_id': facilitator.user_id, 'role': 'facilitator'}]})
    draft, version = affected_selection(owner, program, other_owner, adopter)
    candidate = prepare_candidate(other_owner, adopter['id'], get_program(other_owner, adopter['id'])['revision'])
    release = approve_candidate(other_owner, candidate['id'])
    frozen = tuple(get_db().execute('SELECT snapshot,content_hash,approvals FROM releases WHERE id=?', (release['release_id'],)).fetchone())
    approvals = [tuple(row) for row in get_db().execute('SELECT * FROM candidate_approvals')]
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    revision = get_program(other_owner, adopter['id'])['revision']
    for actor, expected, code in [(facilitator, revision, 403), (owner, revision, 403), (other_owner, revision - 1, 409)]:
        with pytest.raises(DomainError) as denied:
            materials.remove_adoption(actor, adopter['id'], draft['id'], expected)
        assert denied.value.status == code
    other_login(client, csrf_token)
    path = f"/programs/{adopter['id']}/materials"
    response = client.post(path + '/remove', data={'csrf_token': csrf_token(client, path), 'material_id': draft['id'], 'expected_revision': revision})
    assert response.status_code == 302
    assert materials.list_adoptions(other_owner, adopter['id']) == []
    assert get_program(other_owner, adopter['id'])['revision'] == revision + 1
    with pytest.raises(DomainError) as missing:
        materials.remove_adoption(other_owner, adopter['id'], draft['id'], revision + 1)
    assert missing.value.status == 404
    assert get_program(other_owner, adopter['id'])['revision'] == revision + 1
    assert tuple(get_db().execute('SELECT snapshot,content_hash,approvals FROM releases WHERE id=?', (release['release_id'],)).fetchone()) == frozen
    assert [tuple(row) for row in get_db().execute('SELECT * FROM candidate_approvals')] == approvals
    fresh = prepare_candidate(other_owner, adopter['id'], revision + 1)
    assert fresh['state'] == 'pending' and fresh['approvals'] == [] and fresh['materials'] == []


@pytest.mark.parametrize('location', ['document', 'title'])
def test_review_creation_checks_selected_projection_and_revoked_link_stays_revoked(owner, program, submitted, login_client, csrf_token, location):
    if location == 'document':
        save_document(owner, program['id'], 'overview', {'sections': {'purpose': SENTINEL}}, get_program(owner, program['id'])['revision'])
    else:
        update_program(owner, program['id'], {'title': SENTINEL}, get_program(owner, program['id'])['revision'])
    old = create_review_draft(owner, program['id'], ['overview'])
    record_decision(owner, program['id'], {'outcome': SENTINEL, 'rationale': 'Fictional internal decision', 'response_ids': [], 'proposal_ids': []})
    redact_response(owner, submitted['response_ids'][0], 'privacy_request')
    with pytest.raises(DomainError) as rejected:
        create_review_draft(owner, program['id'], ['overview'])
    assert rejected.value.code == 'quarantined_content'
    path = f"/programs/{program['id']}/refinement/reviews"
    response = login_client.post(path, data={'csrf_token': csrf_token(login_client, path), 'document_keys': 'overview'})
    assert response.status_code == 409
    assert len(get_db().execute('SELECT id FROM review_drafts').fetchall()) == 1
    if location == 'document':
        # Unrelated copied document and private decision do not prevent clean selected sharing.
        clean_keys = ['budget']
    else:
        update_program(owner, program['id'], {'title': 'Fictional clean title'}, get_program(owner, program['id'])['revision'])
        clean_keys = ['overview']
    clean = create_review_draft(owner, program['id'], clean_keys)
    assert SENTINEL not in json.dumps(read_review_draft(clean['token']))
    assert login_client.get('/reviews/' + clean['token']).status_code == 200
    assert login_client.get('/reviews/' + old['token']).status_code == 404
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional clean replacement'}}, get_program(owner, program['id'])['revision'])
    replacement = create_review_draft(owner, program['id'], ['overview'])
    assert SENTINEL not in json.dumps(read_review_draft(replacement['token']))


@pytest.mark.parametrize('existing_default', [False, True])
def test_cli_custom_database_backup_restore_account_and_removal(app, owner, program, submitted, tmp_path, capsys, monkeypatch, existing_default):
    from onpf.cli import main
    source = Path(app.config['DATABASE'])
    instance = tmp_path / 'custom-admin-instance'
    default = instance / 'onpf.sqlite3'
    if existing_default:
        from onpf.app import create_app
        from onpf.auth.service import create_user
        unrelated = create_app({'INSTANCE_PATH': str(instance)})
        with unrelated.app_context():
            create_user('unrelated-account', 'fictional-unrelated-password')
    archive = tmp_path / 'custom-PRIVATE.json'
    args = ['--instance', str(instance)]
    assert main(args + ['backup', '--database', str(source), str(archive)]) == 0
    assert str(source.resolve()) in capsys.readouterr().out
    data = json.loads(archive.read_text(encoding='utf-8'))
    assert data['tables']['programs'][0]['id'] == program['id']
    restored = tmp_path / 'restored-custom.sqlite3'
    assert main(['restore', str(archive), str(restored)]) == 0
    db = connect(restored)
    try:
        assert db.execute('SELECT id FROM programs').fetchone()[0] == program['id']
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 2
    finally:
        db.close()
    monkeypatch.setattr('sys.stdin', io.StringIO('fictional-new-password\n'))
    assert main(args + ['create-user', '--database', str(restored), '--username', 'restored-admin', '--password-stdin']) == 0
    assert str(restored.resolve()) in capsys.readouterr().out
    assert main(args + ['redact-response', '--database', str(restored), submitted['response_ids'][0], '--owner', 'owner', '--reason', 'privacy_request']) == 0
    assert json.loads(capsys.readouterr().out)['database'] == str(restored.resolve())
    db = connect(restored)
    try:
        assert db.execute('SELECT text FROM responses WHERE id=?', (submitted['response_ids'][0],)).fetchone()[0] == ''
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3
    finally:
        db.close()
    assert get_db().execute('SELECT text FROM responses WHERE id=?', (submitted['response_ids'][0],)).fetchone()[0] == SENTINEL
    if existing_default:
        db = connect(default)
        try:
            assert [row[0] for row in db.execute('SELECT username FROM users')] == ['unrelated-account']
            assert db.execute('SELECT COUNT(*) FROM programs').fetchone()[0] == 0
        finally:
            db.close()
    else:
        assert not default.exists()


@pytest.mark.parametrize('command', ['backup', 'redact-response'])
@pytest.mark.parametrize('explicit', [False, True])
def test_cli_missing_source_refused_without_initializing(tmp_path, capsys, command, explicit):
    from onpf.cli import main
    instance = tmp_path / 'missing-instance'
    source = tmp_path / 'missing-source.sqlite3'
    destination = tmp_path / 'must-not-exist.json'
    args = ['--instance', str(instance), command]
    if explicit:
        args += ['--database', str(source)]
    args += [str(destination)] if command == 'backup' else ['missing-response', '--owner', 'owner', '--reason', 'privacy_request']
    assert main(args) == 1
    assert 'existing' in capsys.readouterr().err.lower()
    assert not source.exists() and not instance.exists() and not destination.exists()

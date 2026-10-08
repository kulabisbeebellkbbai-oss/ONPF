"""Fictional approval cycles bind exact drafts, input revisions and material terms."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from onpf.db import connect, get_db
from onpf.errors import DomainError
from onpf.programs.service import create_program, get_program, save_document, update_program
from onpf.refinement.service import set_disposition
from onpf.contributions.service import revise_response, submit_responses


def releases():
    from onpf.releases import service
    return service


def prepare(owner, program, **kwargs):
    return releases().prepare_candidate(owner, program['id'], get_program(owner, program['id'])['revision'], **kwargs)


def review(owner, submitted):
    for response_id in submitted['response_ids']:
        set_disposition(owner, response_id, 'deferred', 'Fictional explained deferral for the next design cycle.', [])


def two_owners(owner, other_owner, program):
    return update_program(owner, program['id'], {'owner_ids': [owner.user_id, other_owner.user_id]}, get_program(owner, program['id'])['revision'])


def test_all_owners_must_explicitly_approve(owner, other_owner, program):
    two_owners(owner, other_owner, program)
    candidate = prepare(owner, program)
    assert candidate['approval_rule'] == 'all'
    assert set(candidate['owner_ids']) == {owner.user_id, other_owner.user_id}
    assert candidate['approvals'] == []
    assert releases().approve_candidate(owner, candidate['id'])['state'] == 'pending'
    result = releases().approve_candidate(other_owner, candidate['id'])
    assert result['state'] == 'released'
    release = releases().get_release(owner, result['release_id'])
    assert release['release_number'] == 1
    assert set(a['user_id'] for a in release['approvals']) == {owner.user_id, other_owner.user_id}
    assert release['released_at']


def test_any_owner_rule(owner, other_owner, program):
    two_owners(owner, other_owner, program)
    update_program(owner, program['id'], {'approval_rule': 'any'}, get_program(owner, program['id'])['revision'])
    assert releases().approve_candidate(other_owner, prepare(owner, program)['id'])['state'] == 'released'


@pytest.mark.parametrize('change', ['document', 'owners', 'rule', 'status'])
def test_edit_or_owner_change_invalidates_pending_candidate(owner, other_owner, facilitator, program, change):
    two_owners(owner, other_owner, program)
    candidate = prepare(owner, program)
    releases().approve_candidate(owner, candidate['id'])
    revision = get_program(owner, program['id'])['revision']
    if change == 'document':
        save_document(facilitator, program['id'], 'overview', {'sections': {'purpose': 'Fictional revised purpose'}}, revision)
    else:
        payload = {'owners': {'owner_ids': [owner.user_id, other_owner.user_id, facilitator.user_id]},
                   'rule': {'approval_rule': 'any'}, 'status': {'operating_status': 'confirmed'}}[change]
        update_program(owner, program['id'], payload, revision)
    with pytest.raises(DomainError) as stale:
        releases().approve_candidate(other_owner, candidate['id'])
    assert stale.value.status == 409
    assert get_db().execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
    assert len(releases().get_candidate(owner, candidate['id'])['approvals']) == 1


def test_unreviewed_input_blocks_candidate(owner, program, submitted):
    with pytest.raises(DomainError) as blocked:
        prepare(owner, program)
    assert blocked.value.status == 422
    review(owner, submitted)
    candidate = prepare(owner, program)
    assert len(candidate['response_revision_ids']) == 2
    assert all(row['status'] == 'deferred' for row in candidate['coverage'])


def test_explicit_unreviewed_disposition_still_blocks(owner, program, submitted):
    review(owner, submitted)
    set_disposition(owner, submitted['response_ids'][0], 'unreviewed', 'Fictional still awaiting consideration.', [])
    with pytest.raises(DomainError) as blocked:
        prepare(owner, program)
    assert blocked.value.status == 422


def test_scoped_correction_invalidates_without_program_revision_change(owner, other_owner, program, submitted):
    two_owners(owner, other_owner, program)
    review(owner, submitted)
    candidate = prepare(owner, program)
    releases().approve_candidate(owner, candidate['id'])
    before = get_program(owner, program['id'])['revision']
    revise_response(owner, submitted['response_ids'][0], 'Fictional corrected perspective', 'Fictional requested correction', 1)
    assert get_program(owner, program['id'])['revision'] == before
    with pytest.raises(DomainError) as stale:
        releases().approve_candidate(other_owner, candidate['id'])
    assert stale.value.status == 409
    assert releases().get_candidate(owner, candidate['id'])['response_revision_ids'] == candidate['response_revision_ids']


def test_late_input_is_counted_pending_and_next_cycle_includes_all_current_input(owner, program, batch, submitted):
    review(owner, submitted)
    first = prepare(owner, program)
    late = submit_responses(owner, batch['id'], 'fictional-late', [{'question_id': batch['questions'][0]['id'], 'text': 'Fictional late input'}])
    visible = releases().get_candidate(owner, first['id'])
    assert visible['pending_input_count'] == 1
    release_id = releases().approve_candidate(owner, first['id'])['release_id']
    release = releases().get_release(owner, release_id)
    assert release['response_revision_ids'] == first['response_revision_ids']
    with pytest.raises(DomainError) as blocked:
        prepare(owner, program)
    assert blocked.value.status == 422
    review(owner, late)
    revise_response(owner, submitted['response_ids'][0], 'Fictional newer correction', 'Fictional requested change', 1)
    review(owner, {'response_ids': [submitted['response_ids'][0]]})
    second = prepare(owner, program)
    assert len(second['response_revision_ids']) == 3
    assert set(first['response_revision_ids']) - set(second['response_revision_ids'])
    second_release = releases().get_release(owner, releases().approve_candidate(owner, second['id'])['release_id'])
    assert second_release['release_number'] == 2
    assert releases().get_release(owner, release_id)['response_revision_ids'] == first['response_revision_ids']


def test_repeated_approval_is_idempotent_even_after_new_design_edits(owner, other_owner, program):
    two_owners(owner, other_owner, program)
    candidate = prepare(owner, program)
    assert releases().approve_candidate(owner, candidate['id'])['state'] == 'pending'
    assert releases().approve_candidate(owner, candidate['id'])['state'] == 'pending'
    result = releases().approve_candidate(other_owner, candidate['id'])
    update_program(owner, program['id'], {'purpose': 'Fictional next cycle'}, get_program(owner, program['id'])['revision'])
    assert releases().approve_candidate(owner, candidate['id'])['release_id'] == result['release_id']
    assert get_db().execute('SELECT COUNT(*) FROM candidate_approvals').fetchone()[0] == 2
    assert get_db().execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 1


def test_facilitators_and_outsiders_cannot_approve_or_prepare(owner, other_owner, facilitator, program):
    candidate = prepare(owner, program)
    for actor in [facilitator, other_owner]:
        for action in [lambda: releases().prepare_candidate(actor, program['id'], program['revision']),
                       lambda: releases().approve_candidate(actor, candidate['id'])]:
            with pytest.raises(DomainError) as denied:
                action()
            assert denied.value.status == 403
    assert releases().get_candidate(facilitator, candidate['id'])['id'] == candidate['id']
    with pytest.raises(DomainError) as denied:
        releases().get_candidate(other_owner, candidate['id'])
    assert denied.value.status == 403


def test_stale_prepare_revision_is_atomic(owner, program):
    update_program(owner, program['id'], {'purpose': 'Fictional edit'}, program['revision'])
    with pytest.raises(DomainError) as stale:
        releases().prepare_candidate(owner, program['id'], program['revision'])
    assert stale.value.status == 409
    assert get_db().execute('SELECT COUNT(*) FROM release_candidates').fetchone()[0] == 0


def test_candidates_and_releases_immutable_at_sql_boundary(owner, program):
    candidate = prepare(owner, program)
    release_id = releases().approve_candidate(owner, candidate['id'])['release_id']
    for table, record_id in [('release_candidates', candidate['id']), ('releases', release_id)]:
        for statement in [f'UPDATE {table} SET content_hash=? WHERE id=?', f'DELETE FROM {table} WHERE id=?']:
            with pytest.raises(sqlite3.IntegrityError):
                get_db().execute(statement, ('tampered', record_id) if statement.startswith('UPDATE') else (record_id,))
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('DELETE FROM candidate_approvals WHERE candidate_id=?', (candidate['id'],))


def test_final_approval_rechecks_candidate_hash(owner, program, monkeypatch):
    candidate = prepare(owner, program)
    monkeypatch.setattr(releases(), '_hash', lambda snapshot: 'mismatch')
    with pytest.raises(DomainError) as stale:
        releases().approve_candidate(owner, candidate['id'])
    assert stale.value.status == 409
    assert get_db().execute('SELECT COUNT(*) FROM candidate_approvals').fetchone()[0] == 0


def test_final_approval_waits_for_serialized_response_correction(app, owner, program, submitted):
    review(owner, submitted)
    candidate = prepare(owner, program)
    connection = connect(app.config['DATABASE'])
    connection.execute('BEGIN IMMEDIATE')
    ready = Event()
    def approve_after_lock():
        with app.app_context():
            ready.set()
            try:
                releases().approve_candidate(owner, candidate['id'])
            except DomainError as error:
                return error.status
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(approve_after_lock)
            assert ready.wait(5)
            # This commits while the real service waits to obtain its write lock.
            connection.execute('UPDATE responses SET review_required=1 WHERE id=?', (submitted['response_ids'][0],))
            connection.commit()
            assert result.result(timeout=15) == 409
    finally:
        connection.close()
    assert get_db().execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0


def test_hash_binds_material_terms_and_provenance(owner, program, other_owner):
    from onpf.materials.service import save_material, release_material, adopt_material
    source = create_program(other_owner, {'title': 'Fictional source'})
    draft = save_material(other_owner, source['id'], {'title': 'Fictional source guide', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional identical content'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional own work', 'license': 'MIT', 'notices': 'Fictional notice'}, None)
    version = release_material(other_owner, draft['id'], draft['revision'])
    adopt_material(owner, program['id'], version['id'], program['revision'])
    candidate = prepare(owner, program)
    selected = candidate['materials'][0]
    assert candidate['material_version_ids'] == [version['id']]
    assert selected['license'] == 'MIT'
    assert selected['permission_basis'] == version['permission_basis']
    assert selected['source_terms'] == []
    snapshot = json.loads(get_db().execute('SELECT snapshot FROM release_candidates WHERE id=?', (candidate['id'],)).fetchone()[0])
    original_hash = releases()._hash(snapshot)
    snapshot['materials'][0]['notices'] = 'Fictional changed terms'
    assert releases()._hash(snapshot) != original_hash
    assert candidate['content_hash'] == original_hash


def test_release_framework_labels_and_content_are_frozen(owner, program, monkeypatch):
    candidate = prepare(owner, program)
    expected = candidate['framework']
    assert expected['documents']['overview']['sections'][0]['label']
    monkeypatch.setattr(releases(), 'load_framework', lambda: {'version': 'changed', 'documents': {}})
    release = releases().get_release(owner, releases().approve_candidate(owner, candidate['id'])['release_id'])
    assert release['framework'] == expected


def test_independent_art_release_adoption_and_stable_shared_source(owner, other_owner, program):
    from onpf.materials.service import adopt_material, list_adoptions, material_from_release
    art = create_program(other_owner, {'title': 'Fictional guided art', 'module_keys': ['art']})
    save_document(other_owner, art['id'], 'overview', {'sections': {'purpose': 'Fictional art v1'}}, art['revision'])
    c1 = prepare(other_owner, art)
    r1 = releases().approve_candidate(other_owner, c1['id'])['release_id']
    shared1 = material_from_release(other_owner, r1)
    assert material_from_release(other_owner, r1)['id'] == shared1['id']
    assert shared1['content']['documents']['overview']['sections'][0]['text'] == 'Fictional art v1'
    serialized = json.dumps(shared1['content'])
    for forbidden in ['approvals', 'owner_ids', 'coverage', 'response_revision_ids', 'decision_ids', other_owner.user_id]:
        assert forbidden not in serialized
    adopt_material(owner, program['id'], shared1['id'], program['revision'])
    broad = prepare(owner, program)
    broad_id = releases().approve_candidate(owner, broad['id'])['release_id']
    save_document(other_owner, art['id'], 'overview', {'sections': {'purpose': 'Fictional art v2'}}, get_program(other_owner, art['id'])['revision'])
    r2 = releases().approve_candidate(other_owner, prepare(other_owner, art)['id'])['release_id']
    shared2 = material_from_release(other_owner, r2)
    assert shared2['material_id'] == shared1['material_id']
    assert shared2['version_number'] == 2
    assert releases().get_release(owner, broad_id)['materials'][0]['id'] == shared1['id']
    assert list_adoptions(owner, program['id'])[0]['version_id'] == shared1['id']
    adopt_material(owner, program['id'], shared2['id'], get_program(owner, program['id'])['revision'])
    assert len(list_adoptions(owner, program['id'])) == 1
    assert list_adoptions(owner, program['id'])[0]['version_id'] == shared2['id']


def test_only_source_owner_can_share_release(owner, facilitator, other_owner, program):
    release_id = releases().approve_candidate(owner, prepare(owner, program)['id'])['release_id']
    for actor in [facilitator, other_owner]:
        with pytest.raises(DomainError) as denied:
            releases().material_from_release(actor, release_id)
        assert denied.value.status == 403


def test_pending_operating_status_retained_in_browser_and_release(login_client, csrf_token, owner, program):
    path = f"/programs/{program['id']}/releases"
    token = csrf_token(login_client, path)
    created = login_client.post(path, data={'csrf_token': token, 'expected_revision': program['revision'], 'change_notes': 'Fictional first release <script>literal</script>'})
    assert created.status_code == 302
    page = login_client.get(created.location)
    assert 'Permission to operate: <strong>pending</strong>' in page.text
    assert '&lt;script&gt;literal&lt;/script&gt;' in page.text
    token = csrf_token(login_client, created.location)
    approved = login_client.post(created.location + '/approve', data={'csrf_token': token})
    assert approved.status_code == 302
    page = login_client.get(approved.location)
    assert 'Design approval' in page.text
    assert 'Permission to operate: <strong>pending</strong>' in page.text
    assert page.headers['Cache-Control'] == 'no-store'
    assert 'Fictional first release &lt;script&gt;literal&lt;/script&gt;' in page.text


def test_candidate_form_preserves_stale_revision_and_change_notes(login_client, csrf_token, owner, program):
    path = f"/programs/{program['id']}/releases"
    token = csrf_token(login_client, path)
    update_program(owner, program['id'], {'purpose': 'Fictional intervening edit'}, program['revision'])
    result = login_client.post(path, data={'csrf_token': token, 'expected_revision': program['revision'], 'change_notes': 'Fictional preserved note'})
    assert result.status_code == 409
    assert 'Fictional preserved note' in result.text


def test_browser_pending_count_and_share_action(login_client, csrf_token, owner, program, batch):
    candidate = prepare(owner, program)
    submit_responses(owner, batch['id'], 'fictional-browser-late', [{'question_id': batch['questions'][0]['id'], 'text': 'Fictional late input'}])
    page = login_client.get(f"/candidates/{candidate['id']}")
    assert '1 response' in page.text and 'next cycle' in page.text
    release_id = releases().approve_candidate(owner, candidate['id'])['release_id']
    path = f'/releases/{release_id}'
    token = csrf_token(login_client, path)
    response = login_client.post(path + '/material', data={'csrf_token': token})
    assert response.status_code == 302
    assert login_client.get(response.location).status_code == 200
    assert login_client.post(path + '/material').status_code == 400


def test_disposition_edit_invalidates_pending_approval(owner, other_owner, program, submitted):
    two_owners(owner, other_owner, program)
    review(owner, submitted)
    candidate = prepare(owner, program)
    releases().approve_candidate(owner, candidate['id'])
    set_disposition(owner, submitted['response_ids'][0], 'declined', 'Fictional revised owner consideration.', [])
    with pytest.raises(DomainError) as stale:
        releases().approve_candidate(other_owner, candidate['id'])
    assert stale.value.status == 409


def test_material_adoption_change_invalidates_pending_candidate(owner, other_owner, program):
    from onpf.materials.service import save_material, release_material, adopt_material
    two_owners(owner, other_owner, program)
    candidate = prepare(owner, program)
    releases().approve_candidate(owner, candidate['id'])
    source = create_program(other_owner, {'title': 'Fictional independent source'})
    draft = save_material(other_owner, source['id'], {'title': 'Fictional guide', 'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional content'}, 'ownership_basis': 'own_work', 'permission_basis': 'Fictional authors', 'license': 'MIT', 'notices': ''}, None)
    version = release_material(other_owner, draft['id'], draft['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    with pytest.raises(DomainError) as stale:
        releases().approve_candidate(other_owner, candidate['id'])
    assert stale.value.status == 409


def test_release_documents_remain_stable_after_edit_and_restart(app, owner, program):
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional frozen draft'}}, program['revision'])
    candidate = prepare(owner, program, change_notes='Fictional v1 changes')
    release_id = releases().approve_candidate(owner, candidate['id'])['release_id']
    frozen = releases().get_release(owner, release_id)
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional later draft'}}, get_program(owner, program['id'])['revision'])
    from onpf.app import create_app
    reopened = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'], 'INSTANCE_PATH': app.instance_path})
    with reopened.app_context():
        assert releases().get_release(owner, release_id) == frozen


def test_release_private_to_program_designers(owner, other_owner, facilitator, program):
    release_id = releases().approve_candidate(owner, prepare(owner, program)['id'])['release_id']
    assert releases().get_release(facilitator, release_id)['id'] == release_id
    with pytest.raises(DomainError) as denied:
        releases().get_release(other_owner, release_id)
    assert denied.value.status == 403


def test_frozen_framework_labels_used_when_sharing(owner, program, monkeypatch):
    candidate = prepare(owner, program)
    label = candidate['framework']['documents']['overview']['sections'][0]['label']
    release_id = releases().approve_candidate(owner, candidate['id'])['release_id']
    monkeypatch.setattr(releases(), 'load_framework', lambda: {'version': 'fictional-new-framework', 'documents': {}})
    version = releases().material_from_release(owner, release_id)
    assert version['content']['documents']['overview']['sections'][0]['label'] == label


def test_browser_stale_approval_shows_409_and_retains_frozen_docs(login_client, csrf_token, owner, program):
    candidate = prepare(owner, program, change_notes='Fictional frozen notes')
    path = f"/candidates/{candidate['id']}"
    token = csrf_token(login_client, path)
    update_program(owner, program['id'], {'purpose': 'Fictional changed context'}, program['revision'])
    result = login_client.post(path + '/approve', data={'csrf_token': token})
    assert result.status_code == 409
    assert 'Fictional frozen notes' in result.text
    assert 'fresh candidate' in result.text
    assert 'I approve this exact candidate' not in result.text

"""Inquiry behavior: authority, frozen wording, explicit decisions and invite scope."""
import json
import shutil

import pytest

from onpf.db import get_db
from onpf.errors import DomainError


def service():
    from onpf.inquiries import service as inquiries
    return inquiries


def test_independent_questions_one_batch(owner, program):
    inquiries = service()
    available = inquiries.available_questions(owner, program['id'])
    assert {'core-1-1', 'core-4-3', 'core-7-3'} <= {q['id'] for q in available}
    issued = inquiries.issue_batch(owner, program['id'], {'title': 'Fictional multi-stage inquiry'})
    assert {'core-1-1', 'core-4-3', 'core-7-3'} <= {q['source_id'] for q in issued['questions']}
    assert len(issued['questions']) == 21
    assert issued['version'] == 1


def test_only_decided_dependencies_unlock(owner, facilitator, program):
    inquiries = service()
    updated = inquiries.set_decision_field(owner, program['id'], {'key': 'venue', 'label': 'Chosen venue', 'value': None}, program['revision'])
    question = inquiries.save_question(owner, program['id'], {'text': 'Which garden tools?', 'depends_on': [{'field': 'venue', 'equals': 'garden'}]}, updated['revision'])
    # Contradictory fictional raw inputs are deliberately separate from owner decisions.
    get_db().execute('CREATE TEMP TABLE fictional_raw_responses (answer TEXT)')
    get_db().executemany('INSERT INTO fictional_raw_responses VALUES (?)', [('garden',), ('hall',)])
    assert question['id'] not in {q['id'] for q in inquiries.available_questions(owner, program['id'])}
    catalog = inquiries.question_catalog(owner, program['id'])
    assert next(q for q in catalog if q['id'] == question['id'])['deferred_reason']
    with pytest.raises(DomainError) as forbidden:
        inquiries.set_decision_field(facilitator, program['id'], {'key': 'venue', 'label': 'Chosen venue', 'value': 'garden'}, 3)
    assert forbidden.value.status == 403
    inquiries.set_decision_field(owner, program['id'], {'key': 'venue', 'label': 'Chosen venue', 'value': 'garden'}, 3)
    assert question['id'] in {q['id'] for q in inquiries.available_questions(owner, program['id'])}


def test_prompt_snapshot_is_frozen(owner, program, monkeypatch, tmp_path):
    from onpf.programs import framework
    inquiries = service()
    copied = tmp_path / 'framework'
    shutil.copytree(framework.FRAMEWORK_PATH, copied)
    monkeypatch.setattr(framework, 'FRAMEWORK_PATH', copied)
    batch = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    original = batch['questions'][0]['text']
    module = json.loads((copied / 'core.json').read_text(encoding='utf-8'))
    module['prompts'][0]['text'] = 'Changed future template wording'
    (copied / 'core.json').write_text(json.dumps(module), encoding='utf-8')
    assert inquiries.get_batch(owner, batch['id'])['questions'][0]['text'] == original
    assert inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})['questions'][0]['text'] == 'Changed future template wording'


def test_editable_draft_does_not_change_issued_batch(owner, program):
    inquiries = service()
    inquiries.save_question(owner, program['id'], {'id': 'core-1-1', 'text': 'First local wording'}, 1)
    first = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    inquiries.save_question(owner, program['id'], {'id': 'core-1-1', 'text': 'Second local wording'}, 2)
    assert inquiries.get_batch(owner, first['id'])['questions'][0]['text'] == 'First local wording'
    assert inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})['questions'][0]['text'] == 'Second local wording'


def test_revoked_invitation_denied(owner, program, client, csrf_token):
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {})
    token = inquiries.create_invitation(owner, batch['id'])
    principal = inquiries.resolve_invitation(token)
    assert not principal.user_id and principal.batch_id == batch['id']
    assert get_db().execute('SELECT token_hash FROM invitations WHERE id=?', (principal.invite_id,)).fetchone()[0] != token
    accepted = client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(client, '/invitations/answer')})
    assert accepted.status_code == 302
    assert client.get('/invitations/answer').status_code == 200
    inquiries.revoke_invitation(owner, principal.invite_id)
    response = client.get('/invitations/answer')
    assert response.status_code == 403
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    with pytest.raises(DomainError) as forbidden:
        inquiries.get_batch(principal, batch['id'])
    assert forbidden.value.status == 403


def test_invitation_exposes_only_frozen_intake_and_own_questions(owner, program, client, csrf_token):
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1'], 'instructions': 'Fictional intake instructions'})
    token = inquiries.create_invitation(owner, batch['id'])
    invitee = inquiries.resolve_invitation(token)
    another = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-7-3']})
    with pytest.raises(DomainError) as absent:
        inquiries.get_question(invitee, batch['id'], another['questions'][0]['id'])
    assert absent.value.status == 404
    with pytest.raises(DomainError) as forbidden:
        inquiries.get_batch(invitee, another['id'])
    assert forbidden.value.status == 403
    client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(client, '/invitations/answer')})
    page = client.get('/invitations/answer')
    assert 'Fictional intake instructions' in page.text
    assert 'Which final design decisions' not in page.text
    assert 'fictional-owner' not in page.text and program['id'] not in page.text
    assert 'no-store' in page.headers['Cache-Control']


def test_invitation_identifies_current_program_without_workspace_access(owner, program, client, csrf_token):
    from onpf.programs.service import update_program

    current = update_program(owner, program['id'], {
        'title': 'Fictional <Bridge & Grow>',
        'purpose': 'Build a creative program for local contributors.',
        'local_context': 'A proposed program at the fictional community centre.\nSite approval is pending.',
        'operating_status': 'pending',
    }, program['revision'])
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    token = inquiries.create_invitation(owner, batch['id'])
    client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(client, '/invitations/answer')})

    page = client.get('/invitations/answer')
    assert page.status_code == 200
    assert 'Project you are contributing to' in page.text
    assert 'Working title' in page.text and 'Fictional &lt;Bridge &amp; Grow&gt;' in page.text
    assert 'Purpose' in page.text and 'Build a creative program for local contributors.' in page.text
    assert 'Local context' in page.text and 'Site approval is pending.' in page.text
    assert 'Permission to operate' in page.text and 'Pending' in page.text
    assert '<Bridge & Grow>' not in page.text and program['id'] not in page.text

    update_program(owner, program['id'], {'title': 'Revised fictional title', 'operating_status': 'confirmed'}, current['revision'])
    refreshed = client.get('/invitations/answer')
    assert 'Revised fictional title' in refreshed.text and 'Confirmed' in refreshed.text
    assert 'Fictional &lt;Bridge &amp; Grow&gt;' not in refreshed.text


def test_due_date_advisory_and_closing_revokes_access(owner, program):
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {'due_date': '2000-01-01'})
    token = inquiries.create_invitation(owner, batch['id'])
    assert inquiries.resolve_invitation(token).batch_id == batch['id']
    inquiries.close_batch(owner, batch['id'])
    with pytest.raises(DomainError) as closed:
        inquiries.resolve_invitation(token)
    assert closed.value.status == 403
    assert inquiries.get_batch(owner, batch['id'])['closed_at']


def test_missing_dependency_cycle_and_stale_decision_rejected(owner, program):
    inquiries = service()
    with pytest.raises(DomainError) as missing:
        inquiries.save_question(owner, program['id'], {'text': 'Missing field', 'depends_on': [{'field': 'unknown', 'equals': 'yes'}]}, 1)
    assert missing.value.status == 422
    inquiries.set_decision_field(owner, program['id'], {'key': 'a', 'label': 'A', 'value': 'yes'}, 1)
    inquiries.set_decision_field(owner, program['id'], {'key': 'b', 'label': 'B', 'value': 'yes', 'depends_on': ['a']}, 2)
    with pytest.raises(DomainError) as cycle:
        inquiries.set_decision_field(owner, program['id'], {'key': 'a', 'label': 'A', 'depends_on': ['b']}, 3)
    assert cycle.value.code == 'dependency_cycle'
    with pytest.raises(DomainError) as stale:
        inquiries.set_decision_field(owner, program['id'], {'key': 'a', 'label': 'A', 'value': 'no'}, 1)
    assert stale.value.status == 409
    assert inquiries.decision_fields(owner, program['id'])[0]['value'] == 'yes'


def test_owner_override_requires_reason(owner, facilitator, program):
    inquiries = service()
    inquiries.set_decision_field(owner, program['id'], {'key': 'venue', 'label': 'Venue'}, 1)
    question = inquiries.save_question(owner, program['id'], {'text': 'Which tools?', 'depends_on': [{'field': 'venue', 'equals': 'garden'}]}, 2)
    payload = {'question_ids': [question['id']]}
    with pytest.raises(DomainError) as deferred:
        inquiries.issue_batch(owner, program['id'], payload)
    assert deferred.value.status == 422
    payload['overrides'] = {question['id']: 'Explore before venue choice'}
    with pytest.raises(DomainError) as forbidden:
        inquiries.issue_batch(facilitator, program['id'], payload)
    assert forbidden.value.status == 403
    batch = inquiries.issue_batch(owner, program['id'], payload)
    assert batch['questions'][0]['override_reason'] == 'Explore before venue choice'


def test_outsider_and_forged_custom_question_denied(owner, other_owner, program):
    inquiries = service()
    from onpf.programs.service import create_program
    other = create_program(other_owner, {'title': 'Other fictional program'})
    question = inquiries.save_question(other_owner, other['id'], {'text': 'Private other question'}, 1)
    with pytest.raises(DomainError) as outsider:
        inquiries.available_questions(other_owner, program['id'])
    assert outsider.value.status == 403
    with pytest.raises(DomainError) as forged:
        inquiries.issue_batch(owner, program['id'], {'question_ids': [question['id']]})
    assert forged.value.status == 422


def test_browser_compose_invite_and_print(login_client, owner, program, csrf_token):
    client = login_client
    home = client.get(f'/programs/{program["id"]}')
    assert f'/programs/{program["id"]}/batches' in home.text
    path = f'/programs/{program["id"]}/batches/new'
    page = client.get(path)
    assert 'core-1-1' in page.text and 'core-7-3' in page.text
    response = client.post(path, data={'csrf_token': csrf_token(client, path), 'title': 'Fictional printed inquiry', 'question_ids': ['core-1-1', 'core-4-3', 'core-7-3'], 'instructions': 'Please share multiple ideas.'})
    assert response.status_code == 302
    detail = client.get(response.location)
    assert 'Fictional printed inquiry' in detail.text
    print_page = client.get(response.location + '/print')
    assert print_page.status_code == 200
    assert 'core-1-1' in print_page.text and 'core-7-3' in print_page.text
    assert 'writing-space' in print_page.text
    invited = client.post(response.location + '/invitations', data={'csrf_token': csrf_token(client, response.location)})
    assert invited.status_code == 200 and '/invitations/answer#' in invited.text
    assert '/invitations/answer#' not in client.get(response.location).text


def test_owner_custom_question_forms_preserve_stale_entries(login_client, program, csrf_token):
    client = login_client
    path = f'/programs/{program["id"]}/decisions'
    created = client.post(path, data={'csrf_token': csrf_token(client, path), 'expected_revision': '1', 'key': 'venue', 'label': 'Chosen venue', 'value': 'garden'})
    assert created.status_code == 302
    question_path = f'/programs/{program["id"]}/questions/new'
    drafted = client.post(question_path, data={'csrf_token': csrf_token(client, question_path), 'expected_revision': '2', 'text': '<b>Which tools?</b>', 'stage': '3', 'document_key': 'delivery', 'dep_venue': 'yes', 'equals_venue': 'garden'})
    assert drafted.status_code == 302
    stale = client.post(question_path, data={'csrf_token': csrf_token(client, question_path), 'expected_revision': '2', 'text': 'Retain my stale wording', 'stage': '3', 'document_key': 'delivery'})
    assert stale.status_code == 409 and 'Retain my stale wording' in stale.text
    assert 'name="expected_revision" value="2"' in stale.text
    compose = client.get(f'/programs/{program["id"]}/batches/new')
    assert '&lt;b&gt;Which tools?&lt;/b&gt;' in compose.text
    assert '<b>Which tools?</b>' not in compose.text


def test_forged_invitation_principal_cannot_manage_batch(owner, program):
    from onpf.auth.models import Principal
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {})
    token = inquiries.create_invitation(owner, batch['id'])
    invitee = inquiries.resolve_invitation(token)
    for actor in (invitee, Principal(owner.user_id, invitee.invite_id, batch['id'])):
        with pytest.raises(DomainError) as rejected:
            inquiries.create_invitation(actor, batch['id'])
        assert rejected.value.status == 403
        with pytest.raises(DomainError) as rejected:
            inquiries.close_batch(actor, batch['id'])
        assert rejected.value.status == 403


def test_invalid_invitation_and_csrf_do_not_echo_token(client, csrf_token):
    token = 'fictional-secret-access-code-never-echo'
    response = client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(client, '/invitations/answer')})
    assert response.status_code == 403 and token not in response.text
    response = client.post('/invitations/accept', data={'token': token})
    assert response.status_code == 400 and token not in response.text


def test_owner_login_does_not_expand_invitation_scope(login_client, owner, program, csrf_token):
    inquiries = service()
    batch = inquiries.issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    token = inquiries.create_invitation(owner, batch['id'])
    response = login_client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(login_client, '/invitations/answer')})
    assert response.status_code == 302
    page = login_client.get('/invitations/answer')
    assert 'Program workspace' not in page.text
    assert program['id'] not in page.text
    assert 'Which final design decisions' not in page.text


def test_custom_intake_needs_explicit_owner_approval(owner, facilitator, program):
    inquiries = service()
    with pytest.raises(DomainError) as unapproved:
        inquiries.issue_batch(owner, program['id'], {'intake_copy': 'Fictional custom privacy information'})
    assert unapproved.value.status == 422
    with pytest.raises(DomainError) as forbidden:
        inquiries.issue_batch(facilitator, program['id'], {'intake_copy': 'Fictional custom privacy information', 'intake_copy_approved': True})
    assert forbidden.value.status == 403
    assert inquiries.issue_batch(owner, program['id'], {'intake_copy': 'Fictional custom privacy information', 'intake_copy_approved': True})['intake_copy'] == 'Fictional custom privacy information'


def test_invalid_batch_inputs_leave_no_partial_batch(owner, program):
    inquiries = service()
    for payload in ({'question_ids': []}, {'question_ids': ['core-1-1', 'core-1-1']}, {'due_date': 'tomorrow'}, {'question_ids': ['core-1-1'], 'overrides': {'core-7-3': 'A reason'}}):
        with pytest.raises(DomainError) as rejected:
            inquiries.issue_batch(owner, program['id'], payload)
        assert rejected.value.status == 422
    assert inquiries.list_batches(owner, program['id']) == []


def test_missing_framework_dependency_rejected(owner, program, monkeypatch, tmp_path):
    from onpf.programs import framework
    copied = tmp_path / 'framework'
    shutil.copytree(framework.FRAMEWORK_PATH, copied)
    monkeypatch.setattr(framework, 'FRAMEWORK_PATH', copied)
    module = json.loads((copied / 'core.json').read_text(encoding='utf-8'))
    module['prompts'][0]['depends_on'] = [{'field': 'missing', 'equals': 'yes'}]
    (copied / 'core.json').write_text(json.dumps(module), encoding='utf-8')
    with pytest.raises(DomainError) as rejected:
        service().available_questions(owner, program['id'])
    assert rejected.value.code == 'missing_dependency'

"""Fictional input exercises real storage, authorization and browser forms."""
import re
import pytest

from onpf.db import get_db
from onpf.errors import DomainError


def service():
    from onpf.contributions import service
    return service


def answer(batch, text='Fictional idea', **changes):
    return {'question_id': batch['questions'][0]['id'], 'text': text,
            'answer_state': 'answered', 'attribution': 'anonymous',
            'entry_method': 'direct', **changes}


def test_retry_returns_same_ids(owner, batch):
    first = service().submit_responses(owner, batch['id'], 'retry-1', [answer(batch)])
    retry = service().submit_responses(owner, batch['id'], 'retry-1', [answer(batch)])
    assert first['response_ids'] == retry['response_ids']
    assert first['submission_id'] == retry['submission_id']
    assert get_db().execute('SELECT COUNT(*) FROM responses').fetchone()[0] == 1


def test_new_key_keeps_identical_intentional_response(owner, batch):
    first = service().submit_responses(owner, batch['id'], 'one', [answer(batch)])
    repeat = service().submit_responses(owner, batch['id'], 'two', [answer(batch)])
    assert first['response_ids'] != repeat['response_ids']


def test_multiple_answers_preserved(owner, batch):
    first = service().submit_responses(owner, batch['id'], 'multiple', [answer(batch, 'Fictional A'), answer(batch, 'Fictional B')])
    assert len(first['response_ids']) == 2
    assert [r['text'] for r in service().list_responses(owner, batch['program_id'])] == ['Fictional A', 'Fictional B']


def test_revision_keeps_original(owner, facilitator, batch):
    first = service().submit_responses(owner, batch['id'], 'correction', [answer(batch, 'Fictional original')])
    revised = service().revise_response(facilitator, first['response_ids'][0], 'Fictional corrected', 'Contributor requested correction', 1)
    assert [r['text'] for r in revised['history']] == ['Fictional original', 'Fictional corrected']
    assert revised['revision'] == 2 and revised['review_required'] == 1
    assert revised['history'][1]['entered_by'] == facilitator.user_id
    with pytest.raises(DomainError) as stale:
        service().revise_response(owner, revised['id'], 'Lost stale text', 'Stale request', 1)
    assert stale.value.status == 409
    assert len(service().get_response(owner, revised['id'])['history']) == 2


def test_unicode_markup_and_formula_stored_verbatim(owner, batch):
    literal = '=HYPERLINK("bad")\n<script>alert("é")</script>\n雪 🎨\n  trailing spaces  '
    first = service().submit_responses(owner, batch['id'], 'literal', [answer(batch, literal)])
    record = service().get_response(owner, first['response_ids'][0])
    assert record['text'] == literal and record['history'][0]['text'] == literal


def test_partial_failure_is_atomic(owner, batch):
    with pytest.raises(DomainError) as rejected:
        service().submit_responses(owner, batch['id'], 'partial', [answer(batch), answer(batch, question_id='foreign')])
    assert rejected.value.status == 422
    assert get_db().execute('SELECT COUNT(*) FROM submissions').fetchone()[0] == 0
    assert get_db().execute('SELECT COUNT(*) FROM responses').fetchone()[0] == 0


def test_anonymous_identity_is_omitted_and_permission_defaults_none(owner, batch):
    first = service().submit_responses(owner, batch['id'], 'anonymous', [answer(batch, display_name='Never retain this name')])
    record = service().get_response(owner, first['response_ids'][0])
    assert record['display_name'] is None and record['publication_permission'] == 'none'
    assert 'Never retain this name' not in str([dict(r) for r in get_db().execute('SELECT * FROM responses')])
    assert record['entered_by'] == owner.user_id


def test_conflicting_retry_rejected_and_scoped_by_actor_and_program(owner, facilitator, program, batch):
    first = service().submit_responses(owner, batch['id'], 'same-key', [answer(batch)])
    with pytest.raises(DomainError) as conflict:
        service().submit_responses(owner, batch['id'], 'same-key', [answer(batch, 'Different')])
    assert conflict.value.status == 409
    second = service().submit_responses(facilitator, batch['id'], 'same-key', [answer(batch)])
    assert second['submission_id'] != first['submission_id']
    from onpf.inquiries.service import issue_batch
    another_batch = issue_batch(owner, program['id'], {'question_ids': ['core-1-1']})
    with pytest.raises(DomainError) as conflict:
        service().submit_responses(owner, another_batch['id'], 'same-key', [answer(another_batch)])
    assert conflict.value.status == 409


def test_explicit_states_and_omitted_questions(owner, batch):
    result = service().submit_responses(owner, batch['id'], 'states', [answer(batch, '', answer_state='abstain'), answer(batch, '', answer_state='not_applicable')])
    records = service().list_responses(owner, batch['program_id'])
    assert len(result['response_ids']) == 2
    assert [r['answer_state'] for r in records] == ['abstain', 'not_applicable']
    assert {r['question_id'] for r in records} == {batch['questions'][0]['id']}


def test_invitee_cannot_impersonate_transcriber_or_read_inputs(owner, batch):
    from onpf.inquiries.service import create_invitation, resolve_invitation
    invitee = resolve_invitation(create_invitation(owner, batch['id']))
    result = service().submit_responses(invitee, batch['id'], 'invite', [answer(batch)])
    record = service().get_response(owner, result['response_ids'][0])
    assert record['entered_by'] is None
    for operation in (lambda: service().get_response(invitee, record['id']),
                      lambda: service().list_responses(invitee, batch['program_id']),
                      lambda: service().revise_response(invitee, record['id'], 'text', 'reason', 1)):
        with pytest.raises(DomainError) as forbidden:
            operation()
        assert forbidden.value.status == 403
    for changes in ({'entry_method': 'paper'}, {'entered_by': owner.user_id}, {'facilitator_id': owner.user_id}):
        with pytest.raises(DomainError) as invalid:
            service().submit_responses(invitee, batch['id'], 'forged', [answer(batch, **changes)])
        assert invalid.value.status == 422


def test_cached_invitation_rechecked_and_outsider_denied(owner, other_owner, batch):
    from onpf.inquiries.service import create_invitation, resolve_invitation, revoke_invitation, close_batch
    invitee = resolve_invitation(create_invitation(owner, batch['id']))
    revoke_invitation(owner, invitee.invite_id)
    for actor in (invitee, other_owner):
        with pytest.raises(DomainError) as forbidden:
            service().submit_responses(actor, batch['id'], 'blocked', [answer(batch)])
        assert forbidden.value.status == 403
    close_batch(owner, batch['id'])
    with pytest.raises(DomainError) as closed:
        service().submit_responses(owner, batch['id'], 'closed', [answer(batch)])
    assert closed.value.status == 403


def test_duplicates_remain_traceable_and_cross_program_links_denied(owner, other_owner, batch):
    from onpf.programs.service import create_program
    from onpf.inquiries.service import issue_batch
    first = service().submit_responses(owner, batch['id'], 'dupes', [answer(batch), answer(batch)])
    a, b = first['response_ids']
    service().mark_duplicate(owner, b, a, 'Accidental paper re-entry')
    assert service().get_response(owner, b)['duplicate_of'] == a
    assert len(service().list_responses(owner, batch['program_id'])) == 2
    other = create_program(other_owner, {'title': 'Fictional other program'})
    foreign_batch = issue_batch(other_owner, other['id'], {'question_ids': ['core-1-1']})
    foreign = service().submit_responses(other_owner, foreign_batch['id'], 'foreign', [answer(foreign_batch)])
    with pytest.raises(DomainError) as forbidden:
        service().mark_duplicate(owner, b, foreign['response_ids'][0], 'Foreign link')
    assert forbidden.value.status == 403
    with pytest.raises(DomainError) as cycle:
        service().mark_duplicate(owner, a, b, 'Cycle')
    assert cycle.value.status == 422


@pytest.mark.parametrize('changes', [
    {'text': ''}, {'answer_state': 'wrong'}, {'attribution': 'wrong'},
    {'publication_permission': 'wrong'}, {'attribution': 'named'},
    {'publication_permission': 'attributed_quote'}, {'text': 123},
])
def test_invalid_rows_rejected(owner, batch, changes):
    with pytest.raises(DomainError) as invalid:
        service().submit_responses(owner, batch['id'], 'invalid', [answer(batch, **changes)])
    assert invalid.value.status == 422


def fields(page):
    return {name: value for name, value in re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', page.text)}


def test_browser_invitation_multiple_answers_private_receipt_and_error_recovery(app, login_client, owner, batch, csrf_token):
    from onpf.inquiries.service import create_invitation
    token = create_invitation(owner, batch['id'])
    client = app.test_client()
    client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(client, '/invitations/answer')})
    page = client.get('/invitations/answer')
    assert 'Add another response' in page.text and 'disabled' not in page.text
    values = fields(page)
    text = '<script>bad</script>\nFictional long multiline\n雪 🎨'
    form = {'csrf_token': values['csrf_token'], 'submission_key': values['submission_key'],
            'row_id': ['0', 'extra'], 'question_id_0': batch['questions'][0]['id'],
            'question_id_extra': batch['questions'][0]['id'], 'text_0': text,
            'text_extra': 'Fictional second perspective', 'answer_state_0': 'answered',
            'answer_state_extra': 'answered', 'attribution_0': 'named',
            'attribution_extra': 'anonymous', 'entry_method_0': 'direct', 'entry_method_extra': 'direct'}
    invalid = client.post('/invitations/contributions', data=form)
    assert invalid.status_code == 422 and '&lt;script&gt;bad&lt;/script&gt;' in invalid.text
    assert 'Fictional second perspective' in invalid.text and values['submission_key'] in invalid.text
    form['display_name_0'] = 'Fictional alias'
    posted = client.post('/invitations/contributions', data=form)
    assert posted.status_code == 302 and posted.location.startswith('/invitations/receipts/')
    receipt = client.get(posted.location)
    assert receipt.status_code == 200 and '2 responses' in receipt.text
    assert text not in receipt.text and 'Fictional alias' not in receipt.text
    other_client = app.test_client()
    other_client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(other_client, '/invitations/answer')})
    assert other_client.get(posted.location).status_code == 403
    assert client.get('/responses/' + service().list_responses(owner, batch['program_id'])[0]['id']).status_code == 302
    assert batch['program_id'] not in receipt.text and owner.user_id not in receipt.text


def test_browser_facilitator_history_and_correction(login_client, batch, owner, csrf_token):
    path = f'/batches/{batch["id"]}/contributions'
    page = login_client.get(path)
    assert page.status_code == 200 and 'paper' in page.text
    values = fields(page)
    submitted = login_client.post(path, data={'csrf_token': values['csrf_token'], 'submission_key': values['submission_key'], 'row_id': '0', 'question_id_0': batch['questions'][0]['id'], 'text_0': '<b>Fictional paper entry</b>', 'answer_state_0': 'answered', 'entry_method_0': 'paper', 'attribution_0': 'alias', 'display_name_0': 'Fictional A'})
    assert submitted.status_code == 302
    response_id = service().list_responses(owner, batch['program_id'])[0]['id']
    history_path = f'/responses/{response_id}'
    history = login_client.get(history_path)
    assert '&lt;b&gt;Fictional paper entry&lt;/b&gt;' in history.text
    revised = login_client.post(history_path + '/revise', data={'csrf_token': csrf_token(login_client, history_path), 'expected_revision': '1', 'text': 'Fictional requested correction', 'reason': 'Contributor requested this'})
    assert revised.status_code == 302
    history = login_client.get(history_path)
    assert 'Fictional paper entry' in history.text and 'Fictional requested correction' in history.text


def test_submission_survives_new_app(app, owner, batch):
    from onpf.app import create_app
    result = service().submit_responses(owner, batch['id'], 'restart', [answer(batch)])
    reopened = create_app({'TESTING': True, 'DATABASE': app.config['DATABASE'], 'INSTANCE_PATH': app.instance_path})
    with reopened.app_context():
        assert service().get_response(owner, result['response_ids'][0])['text'] == 'Fictional idea'


def test_intake_does_not_change_design_revision(owner, program, batch):
    service().submit_responses(owner, batch['id'], 'revision', [answer(batch)])
    assert get_db().execute('SELECT revision FROM programs WHERE id=?', (program['id'],)).fetchone()[0] == program['revision']


def test_malformed_question_identity_is_domain_validation(owner, batch):
    with pytest.raises(DomainError) as invalid:
        service().submit_responses(owner, batch['id'], 'malformed', [answer(batch, question_id=[])])
    assert invalid.value.status == 422


def test_per_response_attribution_permissions_and_transcriber(owner, facilitator, batch):
    result = service().submit_responses(facilitator, batch['id'], 'paper-group', [
        answer(batch, 'Fictional group idea', attribution='group', display_name='Fictional group label', entry_method='meeting', publication_permission='attributed_quote'),
        answer(batch, 'Fictional alias idea', attribution='alias', display_name='Fictional alias', entry_method='paper', publication_permission='anonymous_quote'),
    ])
    first, second = [service().get_response(owner, response_id) for response_id in result['response_ids']]
    assert first['attribution'] == 'group' and first['display_name'] == 'Fictional group label'
    assert first['publication_permission'] == 'attributed_quote' and first['entry_method'] == 'meeting'
    assert second['publication_permission'] == 'anonymous_quote' and second['entry_method'] == 'paper'
    assert first['entered_by'] == second['entered_by'] == facilitator.user_id
    assert first['entered_at'].endswith('+00:00')


def test_add_response_without_javascript_preserves_text_and_key(login_client, owner, batch):
    path = f'/batches/{batch["id"]}/contributions'
    page = login_client.get(path)
    values = fields(page)
    added = login_client.post(path, data={'csrf_token': values['csrf_token'], 'submission_key': values['submission_key'], 'row_id': ['0', '1'],
                                      'question_id_0': batch['questions'][0]['id'], 'question_id_1': batch['questions'][1]['id'],
                                      'text_0': 'Fictional retained multiline\nsecond line', 'text_1': 'Other retained response', 'add_question_id': batch['questions'][0]['id']})
    assert added.status_code == 200
    assert 'Fictional retained multiline\nsecond line' in added.text and 'Other retained response' in added.text
    assert added.text.count('class="response-row"') == 3
    assert values['submission_key'] in added.text
    assert service().list_responses(owner, batch['program_id']) == []


def test_invitation_post_ignores_logged_in_account_and_receipt_service_is_private(login_client, owner, batch, csrf_token):
    from onpf.inquiries.service import create_invitation, resolve_invitation
    token = create_invitation(owner, batch['id'])
    actor = resolve_invitation(token)
    login_client.post('/invitations/accept', data={'token': token, 'csrf_token': csrf_token(login_client, '/invitations/answer')})
    values = fields(login_client.get('/invitations/answer'))
    posted = login_client.post('/invitations/contributions', data={'csrf_token': values['csrf_token'], 'submission_key': values['submission_key'], 'row_id': '0',
                                                               'question_id_0': batch['questions'][0]['id'], 'text_0': 'Fictional anonymous direct input'})
    assert posted.status_code == 302
    record = service().list_responses(owner, batch['program_id'])[0]
    assert record['entered_by'] is None
    with pytest.raises(DomainError) as forbidden:
        service().get_receipt(actor, record['submission_id'])
    assert forbidden.value.status == 403
    assert login_client.get(posted.location).status_code == 200


def test_browser_invalid_correction_preserves_all_fields(login_client, owner, batch, csrf_token):
    result = service().submit_responses(owner, batch['id'], 'browser-correction', [answer(batch)])
    path = f'/responses/{result["response_ids"][0]}'
    invalid = login_client.post(path + '/revise', data={'csrf_token': csrf_token(login_client, path), 'expected_revision': '1', 'text': '<b>Keep my correction</b>', 'reason': ''})
    assert invalid.status_code == 422 and '&lt;b&gt;Keep my correction&lt;/b&gt;' in invalid.text
    assert 'name="expected_revision" value="1"' in invalid.text
    assert len(service().get_response(owner, result['response_ids'][0])['history']) == 1


def test_correction_reopens_review_but_keeps_exact_old_revision_scope(owner, batch):
    result = service().submit_responses(owner, batch['id'], 'scope', [answer(batch)])
    response_id = result['response_ids'][0]
    before = service().get_response(owner, response_id)
    get_db().execute('UPDATE responses SET review_required=0 WHERE id=?', (response_id,))
    after = service().revise_response(owner, response_id, 'Fictional revised input', 'Requested correction', 1)
    assert after['review_required'] == 1
    assert after['current_revision_id'] != before['current_revision_id']
    old = get_db().execute('SELECT text FROM response_revisions WHERE id=?', (before['current_revision_id'],)).fetchone()
    assert old['text'] == 'Fictional idea'


def test_duplicate_error_keeps_correction_form_usable(login_client, owner, batch, csrf_token):
    result = service().submit_responses(owner, batch['id'], 'duplicate-error', [answer(batch)])
    path = f'/responses/{result["response_ids"][0]}'
    invalid = login_client.post(path + '/duplicate', data={'csrf_token': csrf_token(login_client, path), 'original_id': result['response_ids'][0], 'duplicate_reason': 'Retain my rejected marker reason'})
    assert invalid.status_code == 422 and 'Retain my rejected marker reason' in invalid.text
    assert 'name="expected_revision" value="1"' in invalid.text
    assert '>Fictional idea</textarea>' in invalid.text

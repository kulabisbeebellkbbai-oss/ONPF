"""Fictional inputs exercise real refinement, authority, revision and privacy boundaries."""
import pytest

from onpf.errors import DomainError


def refinement():
    from onpf.refinement import service
    return service


def proposal(owner, program, response_ids, **changes):
    return refinement().save_proposal(owner, program['id'], {
        'title': 'Fictional option', 'text': 'Fictional public design text',
        'theme': 'Fictional access theme', 'response_ids': response_ids, **changes,
    }, None)


def test_conflicting_ideas_remain_visible(owner, program, submitted):
    service = refinement()
    first, second = submitted['response_ids']
    a = proposal(owner, program, [first])
    b = proposal(owner, program, [second], title='Fictional opposing option')
    service.set_disposition(owner, first, 'incorporated', 'Fictional fit with purpose', [a['id']])
    service.set_disposition(owner, second, 'declined', 'Fictional resource limit', [b['id']])
    rows = service.coverage(owner, program['id'])
    assert {row['status'] for row in rows} == {'incorporated', 'declined'}
    assert [row['text'] for row in rows] == ['Fictional first perspective', 'Fictional opposing perspective']
    assert [row['proposal_ids'] for row in rows] == [[a['id']], [b['id']]]


def test_blank_disposition_reason_rejected(owner, program, submitted):
    with pytest.raises(DomainError) as error:
        refinement().set_disposition(owner, submitted['response_ids'][0], 'declined', '  ', [])
    assert error.value.status == 422
    assert refinement().coverage(owner, program['id'])[0]['status'] == 'unreviewed'


def test_cross_program_link_rejected(owner, other_owner, program, submitted):
    from onpf.programs.service import create_program
    other = create_program(other_owner, {'title': 'Fictional other program'})
    foreign = proposal(other_owner, other, [])
    with pytest.raises(DomainError) as error:
        refinement().set_disposition(owner, submitted['response_ids'][0], 'adapted', 'Fictional reason', [foreign['id']])
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        proposal(other_owner, other, submitted['response_ids'])
    assert error.value.status == 403


def test_facilitator_cannot_make_owner_decision(facilitator, program):
    with pytest.raises(DomainError) as error:
        refinement().record_decision(facilitator, program['id'], {'outcome': 'Fictional choice', 'rationale': 'Fictional reason', 'proposal_ids': [], 'response_ids': []})
    assert error.value.status == 403


def test_response_links_multiple_proposals_and_facilitator_prepares(facilitator, owner, program, submitted):
    first = submitted['response_ids'][0]
    a, b = proposal(facilitator, program, [first]), proposal(facilitator, program, [first])
    refinement().set_disposition(facilitator, first, 'adapted', 'Fictional overall reason accounts for both parts', [a['id'], b['id']])
    row = refinement().coverage(owner, program['id'])[0]
    assert row['proposal_ids'] == [a['id'], b['id']]
    assert row['status'] == 'adapted'


def test_superseding_decision_preserves_predecessor(owner, program, submitted):
    service = refinement()
    a = proposal(owner, program, submitted['response_ids'])
    old = service.record_decision(owner, program['id'], {'outcome': 'Fictional initial choice', 'rationale': 'Fictional initial rationale', 'proposal_ids': [a['id']], 'response_ids': submitted['response_ids']})
    new = service.record_decision(owner, program['id'], {'outcome': 'Fictional revised choice', 'rationale': 'Fictional new information', 'proposal_ids': [a['id']], 'response_ids': [], 'supersedes_id': old['id']})
    rows = service.list_decisions(owner, program['id'])
    assert rows[0]['outcome'] == 'Fictional initial choice'
    assert rows[0]['response_ids'] == submitted['response_ids']
    assert rows[1]['supersedes_id'] == old['id']
    assert rows[1]['id'] == new['id']


def test_correction_reopens_coverage_and_keeps_disposition_history(owner, program, submitted):
    from onpf.contributions.service import get_response, revise_response
    service, response_id = refinement(), submitted['response_ids'][0]
    original = get_response(owner, response_id)
    first = service.set_disposition(owner, response_id, 'deferred', 'Fictional first review', [])
    corrected = revise_response(owner, response_id, 'Fictional corrected current text', 'Fictional requested correction', 1)
    row = service.coverage(owner, program['id'])[0]
    assert row['text'] == 'Fictional corrected current text'
    assert row['status'] == 'unreviewed'
    assert row['disposition_history'][0]['response_revision_id'] == original['current_revision_id']
    second = service.set_disposition(owner, response_id, 'declined', 'Fictional review of correction', [])
    row = service.coverage(owner, program['id'])[0]
    assert row['status'] == 'declined'
    assert [d['id'] for d in row['disposition_history']] == [first['id'], second['id']]
    assert second['response_revision_id'] == corrected['current_revision_id']
    assert get_response(owner, response_id)['review_required'] == 0
    assert get_response(owner, response_id)['original_text'] == 'Fictional first perspective'


def test_stale_disposition_revision_alone_is_unreviewed(owner, program, submitted):
    from onpf.contributions.service import revise_response
    from onpf.db import get_db
    response_id = submitted['response_ids'][0]
    refinement().set_disposition(owner, response_id, 'incorporated', 'Fictional reason', [])
    revise_response(owner, response_id, 'Fictional amended text', 'Fictional correction', 1)
    get_db().execute('UPDATE responses SET review_required=0 WHERE id=?', (response_id,))
    assert refinement().coverage(owner, program['id'])[0]['status'] == 'unreviewed'


def test_duplicate_marker_reopens_classification(owner, program, submitted):
    from onpf.contributions.service import mark_duplicate
    first, second = submitted['response_ids']
    refinement().set_disposition(owner, second, 'declined', 'Fictional reason', [])
    mark_duplicate(owner, second, first, 'Fictional meeting repeated on paper')
    row = refinement().coverage(owner, program['id'])[1]
    assert row['status'] == 'unreviewed'
    assert row['duplicate_of'] == first


def test_design_edits_increment_program_revision_and_stale_proposal_rejected(owner, program, submitted):
    from onpf.programs.service import get_program
    service = refinement()
    before = get_program(owner, program['id'])['revision']
    a = proposal(owner, program, submitted['response_ids'])
    assert get_program(owner, program['id'])['revision'] == before + 1
    edited = service.save_proposal(owner, program['id'], {**a, 'title': 'Fictional updated title'}, a['revision'])
    assert edited['revision'] == 2
    with pytest.raises(DomainError) as error:
        service.save_proposal(owner, program['id'], {**a, 'text': 'Stale text'}, 1)
    assert error.value.status == 409
    service.set_disposition(owner, submitted['response_ids'][0], 'deferred', 'Fictional reason', [])
    service.record_decision(owner, program['id'], {'outcome': 'Fictional choice', 'rationale': 'Fictional reason', 'proposal_ids': [], 'response_ids': []})
    assert get_program(owner, program['id'])['revision'] == before + 4


@pytest.mark.parametrize('status', ['unreviewed', 'incorporated', 'adapted', 'deferred', 'declined'])
def test_exact_disposition_states(owner, program, submitted, status):
    from onpf.contributions.service import get_response
    refinement().set_disposition(owner, submitted['response_ids'][0], status, 'Fictional overall reason', [])
    assert refinement().coverage(owner, program['id'])[0]['status'] == status
    assert get_response(owner, submitted['response_ids'][0])['review_required'] == 0


def test_document_links_decisions_without_changing_document_structure(owner, program):
    from onpf.programs.service import get_program, save_document
    service = refinement()
    decision = service.record_decision(owner, program['id'], {'outcome': 'Fictional adopt quiet option', 'rationale': 'Private fictional deliberation', 'proposal_ids': [], 'response_ids': []})
    current = get_program(owner, program['id'])
    saved = save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional quiet art sessions'}, 'decision_ids': [decision['id']]}, current['revision'])
    assert saved['document_decision_ids']['overview'] == [decision['id']]
    assert set(saved['documents']['overview']) == {'sections'}


def test_review_draft_freezes_only_selected_public_document_content(owner, program, submitted):
    from onpf.programs.service import get_program, save_document
    service = refinement()
    decision = service.record_decision(owner, program['id'], {'outcome': 'Fictional decision', 'rationale': 'PRIVATE FICTIONAL RATIONALE', 'proposal_ids': [], 'response_ids': submitted['response_ids']})
    current = get_program(owner, program['id'])
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': '<b>Fictional public draft</b>'}, 'decision_ids': [decision['id']]}, current['revision'])
    review = service.create_review_draft(owner, program['id'], ['overview'])
    current = get_program(owner, program['id'])
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional later change'}}, current['revision'])
    public = service.read_review_draft(review['token'])
    assert public['documents']['overview']['sections']['purpose'] == '<b>Fictional public draft</b>'
    assert set(public['documents']) == {'overview'}
    assert 'PRIVATE FICTIONAL RATIONALE' not in str(public)
    assert 'Fictional first perspective' not in str(public)
    assert 'Fictional alias' not in str(public)
    assert not ({'program_id', 'created_by', 'token_hash', 'decision_ids'} & set(public))
    service.revoke_review_draft(owner, review['id'])
    with pytest.raises(DomainError) as error:
        service.read_review_draft(review['token'])
    assert error.value.status == 404


def test_review_authority_separate_from_submission_invitation(owner, program, batch):
    from onpf.inquiries.service import create_invitation, resolve_invitation, authorize_batch
    service = refinement()
    invite = create_invitation(owner, batch['id'])
    with pytest.raises(DomainError) as error:
        service.read_review_draft(invite)
    assert error.value.status == 404
    with pytest.raises(DomainError) as error:
        service.create_review_draft(resolve_invitation(invite), program['id'], ['overview'])
    assert error.value.status == 403
    review = service.create_review_draft(owner, program['id'], ['overview'])
    with pytest.raises(DomainError):
        authorize_batch(resolve_invitation(review['token']), batch['id'])


def test_browser_refinement_document_and_public_review(login_client, owner, program, submitted, csrf_token, app):
    service = refinement()
    path = f"/programs/{program['id']}/refinement"
    assert login_client.get(path).status_code == 200
    token = csrf_token(login_client, path + '/proposals/new')
    posted = login_client.post(path + '/proposals/new', data={'csrf_token': token, 'title': 'Fictional <script> option', 'text': 'Fictional drafted option', 'theme': 'Access', 'response_ids': submitted['response_ids']})
    assert posted.status_code == 302
    page = login_client.get(posted.location)
    assert '&lt;script&gt;' in page.text
    assert 'Fictional first perspective' in page.text
    coverage_path = path + '/coverage'
    token = csrf_token(login_client, coverage_path)
    from onpf.contributions.service import get_response
    from onpf.programs.service import get_program
    displayed = get_response(owner, submitted['response_ids'][0])
    bad = login_client.post(coverage_path, data={'csrf_token': token, 'response_id': submitted['response_ids'][0], 'response_revision_id': displayed['current_revision_id'], 'expected_revision': get_program(owner, program['id'])['revision'], 'status': 'declined', 'reason': ' ', 'proposal_ids': []})
    assert bad.status_code == 422
    assert 'declined" selected' in bad.text
    decision = service.record_decision(owner, program['id'], {'outcome': 'Fictional selected option', 'rationale': 'Private fictional rationale', 'proposal_ids': [], 'response_ids': []})
    doc_path = f"/programs/{program['id']}/documents/overview"
    assert decision['id'] in login_client.get(doc_path).text
    reviews_path = path + '/reviews'
    token = csrf_token(login_client, reviews_path)
    posted = login_client.post(reviews_path, data={'csrf_token': token, 'document_keys': ['overview']})
    assert posted.status_code == 200
    import re
    review_path = re.search(r'href="(/reviews/[^"]+)"', posted.text).group(1)
    anonymous = app.test_client()
    page = anonymous.get(review_path)
    assert page.status_code == 200
    assert 'Fictional first perspective' not in page.text
    assert 'Private fictional rationale' not in page.text
    assert 'Sign out' not in page.text
    assert page.headers['Cache-Control'] == 'no-store'
    assert page.headers['Referrer-Policy'] == 'no-referrer'
    assert anonymous.post(review_path).status_code in (400, 405)


def test_invalid_links_are_atomic(owner, program, submitted):
    from onpf.programs.service import get_program
    from onpf.db import get_db
    service = refinement()
    before = get_program(owner, program['id'])['revision']
    with pytest.raises(DomainError):
        proposal(owner, program, [submitted['response_ids'][0], 'unavailable'])
    assert get_db().execute('SELECT count(*) FROM proposals').fetchone()[0] == 0
    assert get_program(owner, program['id'])['revision'] == before
    with pytest.raises(DomainError) as error:
        service.create_review_draft(owner, program['id'], ['overview', 'unknown'])
    assert error.value.status == 422
    assert get_db().execute('SELECT count(*) FROM review_drafts').fetchone()[0] == 0


def test_cross_program_document_decisions_and_supersession_are_rejected(owner, program, other_owner):
    from onpf.programs.service import create_program, get_program, save_document
    other = create_program(other_owner, {'title': 'Fictional independent program'})
    decision = refinement().record_decision(other_owner, other['id'], {'outcome': 'Fictional other choice', 'rationale': 'Fictional reason'})
    current = get_program(owner, program['id'])
    with pytest.raises(DomainError) as error:
        save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Must not save'}, 'decision_ids': [decision['id']]}, current['revision'])
    assert error.value.status == 403
    assert get_program(owner, program['id'])['documents']['overview']['sections']['purpose'] == ''
    with pytest.raises(DomainError) as error:
        refinement().record_decision(owner, program['id'], {'outcome': 'Fictional choice', 'rationale': 'Fictional reason', 'supersedes_id': decision['id']})
    assert error.value.status == 403


def test_stale_browser_disposition_does_not_review_unseen_correction(login_client, owner, program, submitted, csrf_token):
    from onpf.contributions.service import get_response, revise_response
    from onpf.programs.service import get_program
    first = submitted['response_ids'][0]
    path = f"/programs/{program['id']}/refinement/coverage"
    token = csrf_token(login_client, path)
    old = get_response(owner, first)
    revision = get_program(owner, program['id'])['revision']
    revise_response(owner, first, 'Fictional changed meaning', 'Fictional requested edit', 1)
    posted = login_client.post(path, data={'csrf_token': token, 'response_id': first, 'response_revision_id': old['current_revision_id'], 'expected_revision': revision, 'status': 'declined', 'reason': 'Fictional outdated reason'})
    assert posted.status_code == 409
    assert 'Fictional outdated reason' in posted.text
    assert refinement().coverage(owner, program['id'])[0]['status'] == 'unreviewed'
    assert refinement().coverage(owner, program['id'])[0]['disposition_history'] == []


def test_stale_browser_proposal_preserves_entered_fields(login_client, owner, program, submitted, csrf_token):
    a = proposal(owner, program, submitted['response_ids'])
    path = f"/programs/{program['id']}/refinement/proposals/{a['id']}"
    token = csrf_token(login_client, path)
    refinement().save_proposal(owner, program['id'], {**a, 'title': 'Fictional current title'}, 1)
    posted = login_client.post(path, data={'csrf_token': token, 'expected_revision': 1, 'title': 'Fictional stale title', 'text': 'Fictional preserved draft', 'theme': 'Fictional old theme', 'response_ids': submitted['response_ids']})
    assert posted.status_code == 409
    assert 'Fictional preserved draft' in posted.text
    assert 'value="1"' in posted.text
    assert refinement().get_proposal(owner, program['id'], a['id'])['title'] == 'Fictional current title'


@pytest.mark.parametrize('invalid', [[], None, 'accepted'])
def test_malformed_disposition_status_is_domain_error(owner, submitted, invalid):
    with pytest.raises(DomainError) as error:
        refinement().set_disposition(owner, submitted['response_ids'][0], invalid, 'Fictional reason', [])
    assert error.value.status == 422


def test_unrelated_account_cannot_read_or_revoke_private_refinement(owner, other_owner, program, submitted):
    service = refinement()
    review = service.create_review_draft(owner, program['id'], ['overview'])
    for action in (lambda: service.coverage(other_owner, program['id']), lambda: service.list_decisions(other_owner, program['id']), lambda: service.revoke_review_draft(other_owner, review['id'])):
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.status == 403
    assert service.read_review_draft(review['token'])['title'] == 'Fictional test program'


def test_two_opposing_suggestions_reach_editable_document_and_frozen_review(owner, program, submitted):
    from onpf.programs.service import get_program, save_document
    service = refinement()
    first, second = submitted['response_ids']
    a = proposal(owner, program, [first], text='Fictional quieter session')
    b = proposal(owner, program, [second], text='Fictional music session')
    service.set_disposition(owner, first, 'incorporated', 'Fictional quiet access priority', [a['id']])
    service.set_disposition(owner, second, 'deferred', 'Fictional future music session option retained', [b['id']])
    decision = service.record_decision(owner, program['id'], {'outcome': 'Fictional quiet session first', 'rationale': 'Fictional access priority', 'proposal_ids': [a['id'], b['id']], 'response_ids': [first, second]})
    current = get_program(owner, program['id'])
    save_document(owner, program['id'], 'delivery', {'sections': {'activities': 'Fictional quieter session first; music session deferred.'}, 'decision_ids': [decision['id']]}, current['revision'])
    review = service.create_review_draft(owner, program['id'], ['delivery'])
    assert service.read_review_draft(review['token'])['documents']['delivery']['sections']['activities'] == 'Fictional quieter session first; music session deferred.'
    assert [row['original_text'] for row in service.coverage(owner, program['id'])] == ['Fictional first perspective', 'Fictional opposing perspective']


def test_malformed_response_reference_is_domain_error(owner):
    with pytest.raises(DomainError) as error:
        refinement().set_disposition(owner, [], 'deferred', 'Fictional reason', [])
    assert error.value.status == 422


def test_browser_disposition_and_review_revocation(login_client, owner, program, submitted, csrf_token, app):
    from onpf.contributions.service import get_response
    from onpf.programs.service import get_program
    service = refinement()
    path = f"/programs/{program['id']}/refinement/coverage"
    token = csrf_token(login_client, path)
    first = submitted['response_ids'][0]
    response = get_response(owner, first)
    current = get_program(owner, program['id'])
    posted = login_client.post(path, data={'csrf_token': token, 'response_id': first, 'response_revision_id': response['current_revision_id'], 'expected_revision': current['revision'], 'status': 'deferred', 'reason': 'Fictional future cycle'})
    assert posted.status_code == 302
    assert service.coverage(owner, program['id'])[0]['status'] == 'deferred'
    review = service.create_review_draft(owner, program['id'], ['overview'])
    token = csrf_token(login_client, f"/programs/{program['id']}/refinement/reviews")
    revoked = login_client.post(f"/programs/{program['id']}/refinement/reviews/{review['id']}/revoke", data={'csrf_token': token})
    assert revoked.status_code == 302
    assert app.test_client().get('/reviews/' + review['token']).status_code == 404


def test_saved_proposal_records_incorporation_without_manual_coverage_action(owner, program, submitted):
    from onpf.contributions.service import get_response
    from onpf.db import get_db
    first = submitted['response_ids'][0]
    source_revision = get_response(owner, first)['current_revision_id']
    saved = proposal(owner, program, [first])
    row = refinement().coverage(owner, program['id'])[0]
    assert row['status'] == 'incorporated'
    assert row['incorporation_history'][0]['proposal_id'] == saved['id']
    assert row['incorporation_history'][0]['response_revision_id'] == source_revision
    assert row['original_text'] == 'Fictional first perspective'
    assert get_db().execute('SELECT count(*) FROM dispositions').fetchone()[0] == 0
    assert get_response(owner, first)['review_required'] == 0


def test_proposal_edit_keeps_incorporation_history_and_does_not_duplicate_unchanged_links(owner, program, submitted):
    first, second = submitted['response_ids']
    saved = proposal(owner, program, [first])
    edited = refinement().save_proposal(owner, program['id'], {**saved, 'response_ids': [second]}, saved['revision'])
    rows = refinement().coverage(owner, program['id'])
    assert rows[0]['status'] == 'unreviewed'
    assert rows[0]['incorporation_history'][0]['proposal_id'] == edited['id']
    assert rows[1]['status'] == 'incorporated'
    refinement().save_proposal(owner, program['id'], {**edited, 'title': 'Retitled'}, edited['revision'])
    assert len(refinement().coverage(owner, program['id'])[1]['incorporation_history']) == 1


def test_decision_reuses_frozen_proposal_response_revisions(owner, program, submitted):
    from onpf.contributions.service import get_response, revise_response
    first, second = submitted['response_ids']
    original_revision = get_response(owner, first)['current_revision_id']
    saved = proposal(owner, program, [first])
    revise_response(owner, first, 'Fictional revised perspective', 'Correction', 1)
    decided = refinement().record_decision(owner, program['id'], {
        'outcome': 'Fictional choice', 'rationale': 'Fictional reason',
        'proposal_ids': [saved['id']], 'response_ids': [first, second],
    })
    assert decided['response_ids'] == [first, second]
    assert decided['response_links'][0]['response_revision_id'] == original_revision
    updated = refinement().save_proposal(owner, program['id'], {**saved, 'response_ids': [second]}, saved['revision'])
    assert updated['response_ids'] == [second]
    historical = refinement().list_decisions(owner, program['id'])[0]
    assert historical['response_links'][0]['response_revision_id'] == original_revision


def test_decision_preserves_proposal_wording_after_edit(owner, program, submitted, login_client):
    saved = proposal(owner, program, [submitted['response_ids'][0]], text='Fictional original proposal wording')
    decision = refinement().record_decision(owner, program['id'], {
        'outcome': 'Fictional choice', 'rationale': 'Fictional owner reason',
        'proposal_ids': [saved['id']]})
    edited = refinement().save_proposal(owner, program['id'], {**saved,
        'title': 'Fictional retitled proposal', 'text': 'Fictional changed proposal wording'}, saved['revision'])
    frozen = refinement().list_decisions(owner, program['id'])[0]['proposal_snapshots'][0]
    assert frozen['title'] == saved['title']
    assert frozen['text'] == 'Fictional original proposal wording'
    assert frozen['proposal_revision'] == saved['revision']
    assert frozen['capture_basis'] == 'at_decision'
    assert edited['text'] != frozen['text']
    board = login_client.get(f"/programs/{program['id']}/refinement").text
    assert 'Fictional original proposal wording' in board
    assert decision['proposal_snapshots'][0] == frozen


def test_coverage_page_shows_automatic_incorporation_record(login_client, owner, program, submitted):
    saved = proposal(owner, program, [submitted['response_ids'][0]])
    page = login_client.get(f"/programs/{program['id']}/refinement/coverage")
    assert page.status_code == 200
    assert 'Automatically incorporated in saved proposal' in page.text
    assert saved['id'] in page.text


def test_response_references_and_question_context_are_consistent(owner, program, submitted):
    rows = refinement().coverage(owner, program['id'])
    assert [row['reference'] for row in rows] == ['Response 1', 'Response 2']
    assert all(row['question_text'] for row in rows)
    assert rows[0]['question_text'] == rows[1]['question_text']


def test_proposal_evidence_summary_uses_linked_revision(owner, program, submitted):
    from onpf.contributions.service import revise_response
    first = submitted['response_ids'][0]
    saved = proposal(owner, program, [first])
    revise_response(owner, first, 'Fictional corrected meaning', 'Correction', 1)
    groups = refinement().proposal_evidence(owner, program['id'], [saved['id']])
    assert groups[0]['title'] == saved['title']
    assert groups[0]['responses'][0]['reference'] == 'Response 1'
    assert groups[0]['responses'][0]['text'] == 'Fictional first perspective'
    assert groups[0]['responses'][0]['question_text']


def test_refinement_pages_show_discovery_controls_and_readable_history(login_client, owner, program, submitted):
    saved = proposal(owner, program, [submitted['response_ids'][0]])
    refinement().record_decision(owner, program['id'], {
        'outcome': 'Fictional choice', 'rationale': 'Fictional rationale',
        'proposal_ids': [saved['id']], 'response_ids': [],
    })
    root = f"/programs/{program['id']}/refinement"
    coverage = login_client.get(root + '/coverage').text
    assert 'Response 1' in coverage and 'Filter responses' in coverage and 'Sort responses' in coverage
    proposal_page = login_client.get(root + '/proposals/new').text
    assert 'Find source responses' in proposal_page and 'Response 1' in proposal_page
    board = login_client.get(root).text
    assert 'Find additional evidence' in board
    assert 'Response 1' in board and saved['title'] in board
    assert 'Linked evidence from selected proposals' in board

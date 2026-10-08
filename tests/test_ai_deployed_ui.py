"""Native drafting preserves deployed workspace and refinement capabilities."""
from onpf.db import get_db
from onpf.programs.service import get_program
from onpf.refinement import service
from test_ai_routes import generated, state


def test_viewer_keeps_production_workspace_without_edit_or_drafting_controls(login_client, owner, program):
    get_db().execute('UPDATE memberships SET role="viewer" WHERE program_id=? AND user_id=?',
                     (program['id'], owner.user_id))
    root = f'/programs/{program["id"]}'
    page = login_client.get(root)
    assert page.status_code == 200
    assert 'View-only project access' in page.text
    assert root + '/additional' in page.text
    assert '/drafting' not in page.text
    page = login_client.get(root + '/documents/overview')
    assert page.status_code == 200
    assert 'Print view of current text' in page.text
    assert 'name="section_' not in page.text
    assert 'Review drafting assistance' not in page.text
    assert login_client.get(root + '/drafting?kind=proposal').status_code == 403


def test_native_decision_apply_retains_proposal_evidence_and_immutable_history(app, tmp_path, login_client, owner, program, submitted):
    proposal = service.save_proposal(owner, program['id'], {
        'title': 'Decision-time proposal title', 'text': 'Decision-time proposal wording',
        'response_ids': submitted['response_ids']}, None)
    service.record_decision(owner, program['id'], {
        'outcome': 'Earlier owner choice', 'rationale': 'Earlier recorded reason',
        'proposal_ids': [proposal['id']]})
    service.save_proposal(owner, program['id'], {
        'id': proposal['id'], 'title': 'Current proposal title', 'text': 'Current proposal wording',
        'response_ids': submitted['response_ids']}, proposal['revision'])
    program = get_program(owner, program['id'])
    path, result = generated(app, tmp_path, login_client, owner, program, kind='decision',
                             fields={'outcome': 'Current words', 'rationale': 'Current reason'})
    before = get_db().execute('SELECT count(*) FROM decisions').fetchone()[0]
    applied = login_client.post(path + '/apply', data={**state(result.text), 'replace': ['outcome', 'rationale'],
        'current_editor': 'yes', 'current_outcome': 'Current words', 'current_rationale': 'Current reason',
        'current_proposal_ids': [proposal['id']]})
    assert applied.status_code == 200
    assert 'Decision-time proposal wording' in applied.text
    assert 'Current proposal title' in applied.text
    assert 'Linked evidence from selected proposals' in applied.text
    assert 'Fictional first perspective' in applied.text
    assert 'response-filter' in applied.text
    assert 'name="ai_receipt"' in applied.text
    assert 'draft-scope-token' not in applied.text
    assert get_db().execute('SELECT count(*) FROM decisions').fetchone()[0] == before

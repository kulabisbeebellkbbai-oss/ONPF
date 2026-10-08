"""Browser drafting uses real CSRF, native controls and actual fake gateway HTTP."""
import html
import json
import re

import pytest

from onpf.db import get_db
from onpf.drafting import evidence
from test_ai_drafting import server, snapshot, source, suggestion


def hidden(page, name):
    match = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', page)
    assert match, name
    return html.unescape(match.group(1))


def state(page):
    return {name: hidden(page, name) for name in
            ('csrf_token', 'target', 'current_fields', 'expected_revision', 'result', 'evidence_state')}


@pytest.mark.parametrize('kind', ['questions', 'proposal', 'decision', 'document', 'review'])
def test_generate_apply_and_save_are_separate_csrf_actions(app, tmp_path, login_client, owner, program, kind):
    client = login_client
    target = {'kind': kind, **({'document_key': 'overview'} if kind == 'document' else {})}
    path = f'/programs/{program["id"]}/drafting'
    page = client.get(path, query_string=target)
    assert page.status_code == 200
    token = hidden(page.text, 'csrf_token')
    handle = source(owner, program)
    before = snapshot()
    data = {'target': json.dumps(target), 'current_fields': '{}', 'expected_revision': str(program['revision']),
            'handles': handle, 'action': 'generate', 'evidence_state':hidden(page.text,'evidence_state')}
    assert client.post(path, data=data).status_code == 400
    with server(app, tmp_path, json.dumps(suggestion(kind, handle))) as calls:
        page = client.post(path, data={**data, 'csrf_token': token})
    assert page.status_code == 200, page.text
    assert len(calls) == 1
    assert snapshot() == before
    assert 'Selected evidence and current wording' in page.text
    if kind == 'review':
        assert 'Permission remains unresolved.' in page.text
        assert 'Apply selected fields' not in page.text
        assert 'Save review' not in page.text
        return
    applied_data = {**state(page.text), 'replace': list(suggestion(kind, handle)['fields'])}
    if kind == 'questions':
        applied_data.update({'suggested_question_count': '1', 'suggested_q0_text': 'Edited question?',
                             'suggested_q0_stage': '1', 'suggested_q0_document_key': 'overview',
                             'suggested_q0_reason': 'Reviewed reason', 'suggested_q0_source_handles': handle})
    elif kind == 'proposal':
        applied_data.update({'suggested_title': 'Edited option', 'suggested_text': 'Reviewed wording', 'suggested_theme': ''})
    elif kind == 'decision':
        applied_data.update({'suggested_outcome': 'Edited outcome', 'suggested_rationale': 'Reviewed rationale'})
    else:
        key = next(iter(suggestion(kind, handle)['fields']['sections']))
        applied_data['suggested_section_' + key] = 'Reviewed section'
    assert client.post(path + '/apply', data={k:v for k,v in applied_data.items() if k != 'csrf_token'}).status_code == 400
    destination = client.post(path + '/apply', data=applied_data)
    assert destination.status_code == 200, destination.text
    assert snapshot() == before
    assert 'ai_receipt' in destination.text
    token = hidden(destination.text, 'csrf_token')
    receipt = hidden(destination.text, 'ai_receipt')
    if kind == 'questions':
        save_path = f'/programs/{program["id"]}/questions/save-drafts'
        save = {'question_count': '1', 'q0_text': 'Edited question?', 'q0_stage': '1', 'q0_document_key': 'overview',
                'q0_reason': 'Reviewed reason', 'q0_source_handles': handle,
                'sources': hidden(destination.text, 'sources'), 'expected_revision': hidden(destination.text, 'expected_revision')}
    elif kind == 'proposal':
        save_path = f'/programs/{program["id"]}/refinement/proposals/new'
        save = {'title': 'Edited option', 'text': 'Reviewed wording', 'theme': ''}
    elif kind == 'decision':
        save_path = f'/programs/{program["id"]}/refinement'
        save = {'outcome': 'Edited outcome', 'rationale': 'Reviewed rationale'}
    else:
        save_path = f'/programs/{program["id"]}/documents/overview'
        save = {'section_' + key: 'Reviewed section', 'expected_revision': hidden(destination.text, 'expected_revision')}
    save.update({'ai_receipt': receipt, 'csrf_token': token})
    assert client.post(save_path, data={k:v for k,v in save.items() if k != 'csrf_token'}).status_code == 400
    saved = client.post(save_path, data=save)
    assert saved.status_code == 302, saved.text
    assert get_db().execute('SELECT count(*) FROM ai_origins').fetchone()[0] == 1
    assert get_db().execute('SELECT count(*) FROM batches').fetchone()[0] == 0
    duplicate = client.post(save_path, data=save)
    assert duplicate.status_code == 409
    assert get_db().execute('SELECT count(*) FROM ai_origins').fetchone()[0] == 1


def test_failure_preserves_current_wording_and_sanitizes_provider(app, tmp_path, login_client, owner, program):
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=proposal')
    fields = {'title': 'Unfinished title', 'text': '<script>alert(1)</script> keep my wording', 'theme': 'Draft theme'}
    with server(app, tmp_path, 'SECRET-PROVIDER-BODY', status=503) as calls:
        failed = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),
            'target': '{"kind":"proposal"}', 'current_fields': json.dumps(fields), 'handles': source(owner, program),
            'action': 'generate', 'instructions': 'Retain uncertainty'})
    assert failed.status_code == 503
    assert 'Unfinished title' in failed.text and 'keep my wording' in failed.text
    assert '&lt;script&gt;' in failed.text and '<script>alert(1)' not in failed.text
    assert 'SECRET-PROVIDER-BODY' not in failed.text and 'fictional-key' not in failed.text
    assert 'Retain uncertainty' in failed.text and len(calls) == 1


def test_apply_requires_deliberate_replacement(app, tmp_path, login_client, owner, program):
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=proposal')
    handle = source(owner, program)
    with server(app, tmp_path, json.dumps(suggestion('proposal', handle))):
        result = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),
            'target': '{"kind":"proposal"}', 'current_fields': '{"title":"Keep title","text":"Keep words"}',
            'action': 'generate', 'handles': handle})
    before = snapshot()
    refused = login_client.post(path + '/apply', data=state(result.text))
    assert refused.status_code == 422
    assert 'Keep title' in refused.text and 'Keep words' in refused.text
    assert snapshot() == before


@pytest.mark.parametrize('payload', ['{bad', '[]', '{"kind":"proposal","kind":"decision"}'])
def test_malformed_target_safe_refusal(login_client, program, payload):
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=proposal')
    response = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'), 'target': payload})
    assert response.status_code == 422


def test_drafting_requires_account_membership_and_owner_for_decision(client, csrf_token, facilitator, program):
    path = f'/programs/{program["id"]}/drafting'
    assert client.get(path).status_code == 302
    token = csrf_token(client)
    client.post('/login', data={'csrf_token': token, 'username': 'facilitator', 'password': 'fictional-facilitator-password'})
    assert client.get(path + '?kind=decision').status_code == 403
    get_db().execute('UPDATE memberships SET role="contributor" WHERE user_id=?', (facilitator.user_id,))
    assert client.get(path + '?kind=proposal').status_code == 403


def generated(app, tmp_path, client, owner, program, kind='proposal', fields=None, output=None):
    path = f'/programs/{program["id"]}/drafting'
    target = {'kind':kind, **({'document_key':'overview'} if kind == 'document' else {})}
    page = client.get(path, query_string=target)
    handle = source(owner, program)
    with server(app, tmp_path, json.dumps(output or suggestion(kind, handle))):
        page = client.post(path, data={'csrf_token':hidden(page.text,'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'), 'target':json.dumps(target),
            'current_fields':json.dumps(fields or {}), 'handles':handle, 'action':'generate',
            'expected_revision':str(program['revision'])})
    assert page.status_code == 200, page.text
    return path, page


@pytest.mark.parametrize('change', ['stale', 'expired', 'tampered', 'demoted'])
def test_apply_failure_preserves_native_edits_and_never_writes(app, tmp_path, login_client, owner, program, monkeypatch, change):
    path, page = generated(app,tmp_path,login_client,owner,program,fields={'title':'Original','text':'Original words'})
    data = {**state(page.text), 'current_editor':'yes', 'current_title':'Current edited title',
            'current_text':'Current edited wording', 'current_theme':'', 'replace':['title','text'],
            'suggested_title':'Suggestion edit', 'suggested_text':'Suggestion edited words'}
    if change == 'stale':
        get_db().execute('UPDATE programs SET revision=revision+1 WHERE id=?',(program['id'],))
    elif change == 'expired':
        from onpf.drafting import provenance
        monkeypatch.setattr(provenance,'RECEIPT_LIFETIME',-1)
    elif change == 'demoted':
        get_db().execute('UPDATE memberships SET role="contributor" WHERE program_id=? AND user_id=?',(program['id'],owner.user_id))
    else:
        result = json.loads(data['result'])
        result['fields']['text']='Tampered original state'
        data['result']=json.dumps(result)
    before=snapshot()
    refused=login_client.post(path+'/apply',data=data)
    assert refused.status_code == (403 if change == 'demoted' else 422 if change == 'tampered' else 409)
    assert snapshot()==before
    if change != 'demoted':
        assert 'Current edited wording' in refused.text
        assert 'Suggestion edited words' in refused.text


def test_partial_apply_preserves_unselected_native_current_fields(app,tmp_path,login_client,owner,program):
    path,page=generated(app,tmp_path,login_client,owner,program,fields={'title':'Keep title','text':'Keep words','theme':'Keep theme'})
    applied=login_client.post(path+'/apply',data={**state(page.text),'replace':'text',
        'current_editor':'yes','current_title':'Edited current title','current_text':'Edited current words',
        'current_theme':'Edited current theme','suggested_text':'Selected suggestion'})
    assert applied.status_code==200
    assert 'Edited current title' in applied.text and 'Edited current theme' in applied.text
    assert 'Selected suggestion' in applied.text and 'Edited current words' not in applied.text
    assert 'action="/programs/' in applied.text and '/refinement/proposals/new"' in applied.text


def test_followups_require_new_question_generation(app,tmp_path,login_client,owner,program):
    path,page=generated(app,tmp_path,login_client,owner,program,kind='review')
    before=snapshot()
    next_page=login_client.post(path,data={**state(page.text),'action':'followups'})
    assert next_page.status_code==200
    assert json.loads(hidden(next_page.text,'target'))=={'kind':'questions'}
    assert 'Who can confirm the unresolved assumption?' in next_page.text
    assert hidden(next_page.text,'result')==''
    assert 'Apply selected fields' not in next_page.text
    assert snapshot()==before


@pytest.mark.parametrize('fields',[{'sections':[]},{'sections':{'purpose':5}},{'rows':'oops'},{'decision_ids':'oops'}])
def test_malformed_document_fields_safe_refusal(login_client,program,fields):
    path=f'/programs/{program["id"]}/drafting'
    page=login_client.get(path+'?kind=document&document_key=overview')
    response=login_client.post(path,data={'csrf_token':hidden(page.text,'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),
        'target':'{"kind":"document","document_key":"overview"}','current_fields':json.dumps(fields),'action':'edit'})
    assert response.status_code==422


def test_expired_session_and_invitation_cookie_do_not_grant_drafting(login_client,program):
    path=f'/programs/{program["id"]}/drafting'
    get_db().execute('UPDATE auth_sessions SET expires_at="2000-01-01T00:00:00+00:00"')
    login_client.set_cookie('onpf_invitation','fictional-invitation',path='/')
    assert login_client.get(path).status_code==302


def test_default_hints_are_visibly_capped_and_all_sources_available(login_client,program,monkeypatch):
    from onpf.drafting import routes
    sources=[{'handle':f'program:{program["id"]}:{i}','kind':'program','record_key':str(i),'revision':1,
              'label':f'Fictional source {i}','content':{'purpose':f'Visible evidence {i}'},'default_selected':True,'is_addition':False}
             for i in range(105)]
    monkeypatch.setattr(routes.evidence,'catalog',lambda *args:sources)
    page=login_client.get(f'/programs/{program["id"]}/drafting?kind=review')
    assert page.status_code==200
    assert 'Only the first 100 hints are checked' in page.text
    assert page.text.count('name="handles"')==105
    assert len(re.findall(r'name="handles"[^>]*checked',page.text))==100
    assert 'Visible evidence 104' in page.text


@pytest.mark.parametrize('kind', ['proposal','decision','document','questions'])
def test_blank_receipt_is_manual_save(login_client,program,kind):
    token=hidden(login_client.get('/login').text,'csrf_token')
    root=f'/programs/{program["id"]}'
    if kind=='proposal': path,data=root+'/refinement/proposals/new',{'title':'Manual','text':'Manual wording'}
    elif kind=='decision': path,data=root+'/refinement',{'outcome':'Manual choice','rationale':'Manual reason'}
    elif kind=='document': path,data=root+'/documents/overview',{'expected_revision':program['revision']}
    else: path,data=root+'/questions/new',{'text':'Manual question?','stage':'1','document_key':'overview','expected_revision':program['revision']}
    response=login_client.post(path,data={**data,'csrf_token':token,'ai_receipt':''})
    assert response.status_code==302,response.text
    assert get_db().execute('SELECT count(*) FROM ai_origins').fetchone()[0]==0


def test_entry_actions_preserve_unsaved_wording_and_manual_disabled_forms(login_client,owner,program,submitted):
    root=f'/programs/{program["id"]}'
    page=login_client.get(root+'/refinement/proposals/new')
    assert 'Review drafting assistance with current wording' in page.text
    preview=login_client.post(root+'/drafting',data={'csrf_token':hidden(page.text,'csrf_token'),
        'draft_kind':'proposal','title':'Unsaved native title','text':'Unsaved native words','theme':'Native theme'})
    assert preview.status_code==200
    assert 'Unsaved native title' in preview.text and 'Unsaved native words' in preview.text
    assert 'AI drafting is disabled' in preview.text
    assert 'Save proposal' in page.text
    assert get_db().execute('SELECT count(*) FROM proposals').fetchone()[0]==0


def test_disabled_invalid_destination_does_not_echo_embedded_secret(app,login_client,program):
    app.config['AI_GATEWAY_URL']='https://username:SECRET-CREDENTIAL@example.test/v1?SECRET-QUERY'
    page=login_client.get(f'/programs/{program["id"]}/drafting?kind=proposal')
    assert page.status_code==200
    assert 'SECRET-CREDENTIAL' not in page.text and 'SECRET-QUERY' not in page.text


def test_existing_proposal_direct_drafting_retains_its_revision(app,tmp_path,login_client,owner,program):
    from onpf.refinement import service
    proposal=service.save_proposal(owner,program['id'],{'title':'Existing','text':'Existing wording'},None)
    page=login_client.get(f'/programs/{program["id"]}/drafting',query_string={'kind':'proposal','record_key':proposal['id']})
    assert hidden(page.text,'expected_revision')==str(proposal['revision'])


def test_question_entry_retains_existing_context_and_unsaved_dependencies(login_client,owner,program):
    from onpf.inquiries import service,clarifications
    from onpf.programs.service import get_program
    service.set_decision_field(owner,program['id'],{'key':'venue','label':'Venue','value':'confirmed'},program['revision'])
    revision=get_program(owner,program['id'])['revision']
    question=service.save_question(owner,program['id'],{'text':'Existing question?','stage':1,'document_key':'overview','answer_type':'text'},revision)
    handle=source(owner,program)
    manifest=evidence.select(owner,program['id'],{'kind':'questions'},[handle])['sources']
    clarifications.save_question_context(owner,program['id'],question['id'],'Existing question reason',manifest)
    path=f'/programs/{program["id"]}/drafting'
    page=login_client.get(f'/programs/{program["id"]}/questions/{question["id"]}')
    preview=login_client.post(path,data={'csrf_token':hidden(page.text,'csrf_token'),'draft_kind':'questions',
        'draft_record_key':question['id'],'text':'Unsaved question words?','stage':'1','document_key':'overview',
        'dep_venue':'yes','equals_venue':'tentative','expected_revision':str(revision)})
    assert preview.status_code==200
    assert 'Existing question reason' in preview.text
    assert json.loads(hidden(preview.text,'destination_state'))=={'depends_on':[{'field':'venue','equals':'tentative'}]}


@pytest.mark.parametrize('change',['correction','removal','demotion'])
def test_generation_mid_http_changes_fail_without_losing_current_words(app,tmp_path,login_client,owner,program,submitted,change):
    path=f'/programs/{program["id"]}/drafting'
    page=login_client.get(path+'?kind=proposal')
    handle=source(owner,program,'response',submitted['response_ids'][0])
    def during():
        with app.app_context():
            if change=='correction':
                from onpf.contributions.service import revise_response
                revise_response(owner,submitted['response_ids'][0],'Corrected evidence','Fictional reason',1)
            elif change=='removal':
                from onpf.archives.service import redact_response
                redact_response(owner,submitted['response_ids'][0],'privacy_request')
            else:
                get_db().execute('UPDATE memberships SET role="contributor" WHERE user_id=?',(owner.user_id,))
    with server(app,tmp_path,json.dumps(suggestion('proposal',handle)),during) as calls:
        refused=login_client.post(path,data={'csrf_token':hidden(page.text,'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),
            'target':'{"kind":"proposal"}','current_fields':'{"title":"Keep private title","text":"Keep private current words"}',
            'action':'generate','handles':handle})
    assert refused.status_code==(403 if change=='demotion' else 409)
    assert len(calls)==1
    assert get_db().execute('SELECT count(*) FROM proposals').fetchone()[0]==0
    assert get_db().execute('SELECT count(*) FROM ai_origins').fetchone()[0]==0
    assert 'Keep private current words' in refused.text
    if change=='removal':
        assert 'Previously selected source is unavailable' in refused.text
        assert 'Fictional first perspective' not in refused.text


@pytest.mark.parametrize('document_key',['adoption','budget','delivery','feedback','overview','session-plan','volunteers'])
def test_document_native_editor_actual_http_apply(app,tmp_path,login_client,owner,program,document_key):
    from onpf.programs.framework import load_framework
    path=f'/programs/{program["id"]}/drafting'
    target={'kind':'document','document_key':document_key}
    page=login_client.get(path,query_string=target)
    handle=source(owner,program)
    output=suggestion('document',handle,document_key)
    if document_key=='budget':
        output['fields']['rows']=[{'item':'Fictional resource','quantity':'2','unit_cost':None,'cost_status':'estimated','notes':'Unknown cost'}]
    before=snapshot()
    with server(app,tmp_path,json.dumps(output)) as calls:
        result=login_client.post(path,data={'csrf_token':hidden(page.text,'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),'target':json.dumps(target),
            'current_fields':'{}','handles':handle,'action':'generate','expected_revision':str(program['revision'])})
    assert result.status_code==200 and len(calls)==1
    applied=login_client.post(path+'/apply',data={**state(result.text),'replace':list(output['fields'])})
    assert applied.status_code==200
    for section in load_framework()['documents'][document_key]['sections']:
        assert 'name="section_'+section['key']+'"' in applied.text
    if document_key=='budget':
        assert 'Fictional resource' in applied.text and 'Unknown cost' in applied.text
        assert 'name="unit_cost_0" value=""' in applied.text
    assert snapshot()==before


def test_native_bulk_save_error_preserves_words_and_receipt(login_client,program):
    path=f'/programs/{program["id"]}/questions/save-drafts'
    token=hidden(login_client.get('/login').text,'csrf_token')
    before=snapshot()
    page=login_client.post(path,data={'csrf_token':token,'expected_revision':program['revision'],
        'question_count':'1','q0_text':'Retain question wording?','q0_stage':'1','q0_document_key':'overview',
        'q0_reason':'Retain context','sources':'[]','ai_receipt':'invalid-receipt'})
    assert page.status_code==409
    assert 'Retain question wording?' in page.text and 'Retain context' in page.text
    assert hidden(page.text,'ai_receipt')=='invalid-receipt'
    assert snapshot()==before


def test_public_review_and_invites_exclude_drafting_entry(login_client,owner,program,batch):
    from onpf.inquiries.service import create_invitation
    from onpf.refinement.service import create_review_draft
    invite=create_invitation(owner,batch['id'])
    token=hidden(login_client.get('/login').text,'csrf_token')
    login_client.post('/invitations/accept',data={'csrf_token':token,'token':invite})
    page=login_client.get('/invitations/answer')
    assert '/drafting' not in page.text
    review=create_review_draft(owner,program['id'],['overview'])
    page=login_client.get('/reviews/'+review['token'])
    assert '/drafting' not in page.text


def test_server_edit_is_a_csrf_action_and_preserves_both_editors(app,tmp_path,login_client,owner,program):
    path,page=generated(app,tmp_path,login_client,owner,program,fields={'title':'Current title','text':'Current words'})
    before=snapshot()
    data={**state(page.text),'action':'edit','current_editor':'yes','current_title':'Edited current title',
        'current_text':'Edited current words','current_theme':'Current grouping','suggested_editor':'yes',
        'suggested_title':'Edited suggested title','suggested_text':'Edited suggested words','suggested_theme':'Suggested grouping'}
    assert login_client.post(path,data={k:v for k,v in data.items() if k!='csrf_token'}).status_code==400
    edited=login_client.post(path,data=data)
    assert edited.status_code==200
    assert 'Edited current title' in edited.text and 'Edited current words' in edited.text
    assert 'Edited suggested title' in edited.text and 'Edited suggested words' in edited.text
    assert hidden(edited.text,'result')==hidden(page.text,'result')
    assert snapshot()==before


def test_stale_normal_save_preserves_native_destination_words(app,tmp_path,login_client,owner,program):
    path,page=generated(app,tmp_path,login_client,owner,program)
    applied=login_client.post(path+'/apply',data={**state(page.text),'replace':['title','text']})
    assert applied.status_code==200
    get_db().execute('UPDATE programs SET revision=revision+1 WHERE id=?',(program['id'],))
    before=snapshot()
    refused=login_client.post(f'/programs/{program["id"]}/refinement/proposals/new',data={
        'csrf_token':hidden(applied.text,'csrf_token'),'ai_receipt':hidden(applied.text,'ai_receipt'),
        'title':'Last edited title','text':'Last edited wording','theme':'Retained theme'})
    assert refused.status_code==409
    assert 'Last edited title' in refused.text and 'Last edited wording' in refused.text
    assert 'Retained theme' in refused.text
    assert hidden(refused.text,'ai_receipt')==hidden(applied.text,'ai_receipt')
    assert snapshot()==before


def test_unavailable_current_links_are_not_silently_dropped(login_client,program):
    path=f'/programs/{program["id"]}/drafting'
    page=login_client.get(path+'?kind=proposal')
    edited=login_client.post(path,data={'csrf_token':hidden(page.text,'csrf_token'), 'evidence_state':hidden(page.text,'evidence_state'),'target':'{"kind":"proposal"}',
        'current_fields':json.dumps({'title':'Keep title','text':'Keep words','response_ids':['unavailable-response']}),
        'action':'edit'})
    assert edited.status_code==200
    assert 'Unavailable linked source: unavailable-response' in edited.text
    assert 'name="current_response_ids" value="unavailable-response" checked' in edited.text


def test_existing_decision_target_starts_as_explicit_supersession(login_client,owner,program):
    from onpf.refinement.service import record_decision
    decision=record_decision(owner,program['id'],{'outcome':'Original choice','rationale':'Original rationale'})
    page=login_client.get(f'/programs/{program["id"]}/drafting',query_string={'kind':'decision','record_key':decision['id']})
    assert page.status_code==200
    assert json.loads(hidden(page.text,'current_fields'))['supersedes_id']==decision['id']
    assert 'Original choice' in page.text and 'Original rationale' in page.text


def test_stale_retry_rebases_save_reference_only_after_explicit_choice(app,tmp_path,login_client,owner,program):
    path,page=generated(app,tmp_path,login_client,owner,program,kind='document',fields={'sections':{'purpose':'Retained original wording'}})
    get_db().execute('UPDATE programs SET revision=revision+1 WHERE id=?',(program['id'],))
    old=state(page.text)
    handle=source(owner,program)
    with server(app,tmp_path,json.dumps(suggestion('document',handle))):
        unchosen=login_client.post(path,data={**old,'handles':handle,'action':'generate'})
        chosen=login_client.post(path,data={**old,'handles':handle,'action':'generate','refresh_revision':'yes'})
    assert unchosen.status_code==409 and chosen.status_code==200
    assert hidden(unchosen.text,'expected_revision')==str(program['revision'])
    assert 'Use the current save revision' in unchosen.text
    assert hidden(chosen.text,'expected_revision')==str(program['revision']+1)
    assert 'Retained original wording' in chosen.text


@pytest.mark.parametrize('existing_ids', [False, True])
def test_blank_first_native_question_retains_slots_identities_dependencies_and_retry(login_client,owner,program,existing_ids):
    from onpf.inquiries import service
    from onpf.programs.service import get_program
    service.set_decision_field(owner,program['id'],{'key':'venue','label':'Venue','value':'pending'},program['revision'])
    identities=[]
    if existing_ids:
        for text in ('Existing first question?','Existing second question?'):
            saved=service.save_question(owner,program['id'],{'text':text,'stage':1,'document_key':'overview','answer_type':'text'},
                                        get_program(owner,program['id'])['revision'])
            identities.append(saved['id'])
    revision=get_program(owner,program['id'])['revision']
    handle=source(owner,program)
    manifests=evidence.select(owner,program['id'],{'kind':'questions'},[handle])['sources']
    path=f'/programs/{program["id"]}/questions/save-drafts'
    token=hidden(login_client.get('/login').text,'csrf_token')
    data={'csrf_token':token,'expected_revision':str(revision),'question_count':'2',
        'q0_text':'','q0_reason':'','q0_stage':'4','q0_document_key':'delivery',
        'q1_text':'Second retained question?','q1_reason':'Second retained context','q1_stage':'7','q1_document_key':'adoption',
        'q1_source_handles':handle,'q1_dep_venue':'yes','q1_equals_venue':'confirmed',
        'sources':json.dumps(manifests),'ai_receipt':''}
    if existing_ids:
        data.update({'q0_id':identities[0],'q1_id':identities[1]})
    before=snapshot()
    refused=login_client.post(path,data=data)
    assert refused.status_code==422
    assert snapshot()==before
    assert hidden(refused.text,'question_count')=='2'
    assert re.search(r'name="q0_text"[^>]*>\s*</textarea>',refused.text)
    assert re.search(r'name="q1_text"[^>]*>Second retained question\?</textarea>',refused.text)
    assert re.search(r'name="q1_reason"[^>]*>Second retained context</textarea>',refused.text)
    assert re.search(r'name="q1_dep_venue"[^>]*checked',refused.text)
    assert hidden(refused.text,'q1_equals_venue')=='confirmed'
    assert re.search(r'name="q1_source_handles"[^>]*value="'+re.escape(handle)+r'"[^>]*checked',refused.text)
    if existing_ids:
        assert hidden(refused.text,'q0_id')==identities[0]
        assert hidden(refused.text,'q1_id')==identities[1]
    retry={**data,'csrf_token':hidden(refused.text,'csrf_token'),'expected_revision':hidden(refused.text,'expected_revision'),
        'question_count':hidden(refused.text,'question_count'),'q0_text':'Filled first question?',
        'q1_equals_venue':hidden(refused.text,'q1_equals_venue'),'sources':hidden(refused.text,'sources')}
    saved=login_client.post(path,data=retry)
    assert saved.status_code==302,saved.text
    records={row['id']:json.loads(row['content']) for row in get_db().execute('SELECT id,content FROM inquiry_questions')}
    assert len(records)==2
    first=records[identities[0]] if existing_ids else next(q for q in records.values() if q['text']=='Filled first question?')
    second=records[identities[1]] if existing_ids else next(q for q in records.values() if q['text']=='Second retained question?')
    assert first['text']=='Filled first question?' and first['stage']==4 and first['document_key']=='delivery'
    assert second['text']=='Second retained question?' and second['stage']==7 and second['document_key']=='adoption'
    assert second['depends_on']==[{'field':'venue','equals':'confirmed'}]
    assert get_db().execute('SELECT count(*) FROM batches').fetchone()[0]==0
    assert get_db().execute('SELECT count(*) FROM ai_origins').fetchone()[0]==0

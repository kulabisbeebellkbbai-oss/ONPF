"""Acceptance regressions for the combined Bridge Cafe update."""
import json
import re
import pytest

from onpf.db import get_db
from onpf.drafting import evidence


def test_drafting_sharing_copy_and_readable_response(login_client, owner, program, submitted):
    page = login_client.get(f"/programs/{program['id']}/drafting?kind=proposal")
    assert page.status_code == 200
    assert 'Configured gateway destination' not in page.text
    assert 'drafting gateway' not in page.text
    assert 'Response 1' in page.text
    assert 'Fictional first perspective' in page.text
    assert 'Find evidence' in page.text


def test_proposal_browser_title_has_no_origin_markup(login_client, owner, program, submitted):
    from onpf.refinement.service import save_proposal
    proposal = save_proposal(owner, program['id'], {'title':'Test option','text':'Test text','response_ids':[]}, None)
    page = login_client.get(f"/programs/{program['id']}/refinement/proposals/{proposal['id']}")
    assert page.status_code == 200
    title = re.search(r'<title>(.*?)</title>', page.text, re.S).group(1)
    assert '<' not in title
    assert 'Â' not in title


def test_decision_drafting_inherits_proposal_support(login_client, owner, program, submitted, csrf_token):
    from onpf.refinement.service import save_proposal
    ids = submitted['response_ids']
    proposal = save_proposal(owner, program['id'], {'title':'Supported option','text':'Text','response_ids':ids}, None)
    page = login_client.post(f"/programs/{program['id']}/drafting", data={
        'csrf_token':csrf_token(login_client), 'draft_kind':'decision',
        'outcome':'Unsaved outcome','rationale':'Unsaved rationale','proposal_ids':proposal['id']})
    assert page.status_code == 200
    for key in ids:
        handle = f"response:{program['id']}:{key}"
        assert re.search(r'name="handles" value="' + re.escape(handle) + r'" checked', page.text)
    excluded = login_client.post(f"/programs/{program['id']}/drafting", data={
        'csrf_token':csrf_token(login_client), 'target':json.dumps({'kind':'decision'}),
        'current_fields':json.dumps({'outcome':'Outcome','rationale':'Reason','proposal_ids':[proposal['id']]}),
        'selection_reviewed':'yes','action':'edit'})
    assert not re.search(r'name="handles"[^>]* checked', excluded.text)


def test_custom_question_handoff_keeps_rationale_source_group(login_client, program, csrf_token):
    page = login_client.post(f"/programs/{program['id']}/drafting", data={
        'csrf_token':csrf_token(login_client), 'draft_kind':'questions',
        'text':'Distinctive wording','why':'Existing staff; controlled portions',
        'source':f"program|{program['id']}", 'group_label':'Kitchen arrangements',
        'stage':'2','document_key':'overview'})
    assert page.status_code == 200
    assert 'Existing staff; controlled portions' in page.text
    assert 'Kitchen arrangements' in page.text
    assert re.search(r'name="current_q0_source_handles"[^>]* checked', page.text)


def test_inherited_support_keeps_historical_response_revision(owner, program, submitted):
    from onpf.refinement.service import save_proposal
    from onpf.contributions.service import revise_response
    from onpf.auth.service import get_principal
    response_id = submitted['response_ids'][0]
    proposal = save_proposal(owner, program['id'], {'title':'Original support','text':'Text','response_ids':[response_id]}, None)
    revise_response(owner, response_id, 'Changed response', 'Correction', 1)
    sources = evidence.catalog(owner, program['id'], {'kind':'decision'})
    inherited = evidence.inherited_handles(sources, {'proposal_ids':[proposal['id']]})
    selected = [s for s in sources if s['handle'] in inherited and s['kind']=='response']
    assert len(selected) == 1
    assert selected[0]['revision'] == 1
    assert selected[0]['content']['text'] == 'Fictional first perspective'
    assert selected[0]['content']['historical'] is True


def test_organizer_clarification_revision_and_selection(owner, other_owner, program):
    from onpf.drafting import organizer
    first = organizer.save(owner, program['id'], 'Existing staff and controlled portions', {'kind':'questions'})
    second = organizer.save(owner, program['id'], 'Existing on-duty staff; coordinator controls portions', {'kind':'questions'}, first['id'], 1)
    assert second['revision'] == 2
    assert organizer.history(owner, program['id'], first['id'])[0]['text'] == first['text']
    sources = evidence.catalog(owner, program['id'], {'kind':'questions'})
    clarification = next(s for s in sources if s['kind']=='organizer_clarification')
    assert clarification['content']['text'] == second['text']
    assert clarification['default_selected']
    assert evidence.select(owner, program['id'], {'kind':'questions'}, [])['sources'] == []
    with pytest.raises(Exception) as error:
        organizer.save(other_owner, program['id'], 'Forbidden', {'kind':'questions'})
    assert getattr(error.value,'status',None) == 403


def test_budget_reservation_reconciliation_and_unknown_pricing(app, owner, program):
    from onpf.drafting import usage
    app.config['AI_DRAFTING_ENABLED']=True
    usage.set_limits(owner,'system','',{'daily_budget_micro':'100','input_micro_per_1000':'1000','output_micro_per_1000':'1000','priced_model':'onpf-drafting'})
    reservation = usage.reserve(owner,program['id'],50,50,50)
    usage.finish(reservation, {'prompt_tokens':25,'completion_tokens':25})
    with pytest.raises(Exception) as error:
        usage.reserve(owner,program['id'],30,30,30)
    assert getattr(error.value,'code',None) == 'ai_budget_exhausted'
    assert get_db().execute('SELECT charged_micro FROM ai_usage WHERE id=?',(reservation,)).fetchone()[0] == 50
    usage.set_limits(owner,'system','',{'daily_budget_micro':'100','input_micro_per_1000':'','output_micro_per_1000':'','priced_model':''})
    with pytest.raises(Exception) as unknown:
        usage.reserve(owner,program['id'],1,1,1)
    assert getattr(unknown.value,'code',None) == 'ai_pricing_unknown'


def test_failed_attempt_without_usage_retains_cost_reservation(app,owner,program):
    from onpf.drafting import usage
    app.config['AI_DRAFTING_ENABLED']=True
    usage.set_limits(owner,'system','',{'daily_budget_micro':'100','input_micro_per_1000':'1000','output_micro_per_1000':'1000','priced_model':'onpf-drafting'})
    reservation=usage.reserve(owner,program['id'],50,50,50)
    usage.finish(reservation,None)
    row=get_db().execute('SELECT * FROM ai_usage WHERE id=?',(reservation,)).fetchone()
    assert row['charged_micro']==100
    assert row['state']=='uncertain'


def test_initial_document_guards_and_existing_section_preservation(app, tmp_path, owner, program):
    from test_ai_drafting import server, suggestion, source
    from onpf.drafting.service import generate
    from onpf.drafting.guards import GUARD
    from onpf.programs.service import save_document
    from onpf.releases.service import prepare_candidate
    handle = source(owner, program)
    raw = suggestion('document', handle)
    raw['fields']['sections']['purpose'] = 'Generated initial purpose'
    with server(app, tmp_path, json.dumps(raw)):
        result = generate(owner, program['id'], {'kind':'document','document_key':'overview'}, [handle], '', {})
    assert all(GUARD in text for text in result['fields']['sections'].values() if text.strip())
    saved = save_document(owner, program['id'], 'overview', result['fields'], program['revision'])
    with pytest.raises(Exception) as error:
        prepare_candidate(owner, program['id'], saved['revision'])
    assert getattr(error.value, 'code', None) == 'unreviewed_sections'
    with server(app, tmp_path, json.dumps(raw)):
        revised = generate(owner, program['id'], {'kind':'document','document_key':'overview','mode':'initial'}, [handle], '', {'sections':{'purpose':'Reviewed original purpose'}})
    assert revised['fields']['sections']['purpose'] == 'Reviewed original purpose'


def test_admin_app_and_access_removal_preserve_attribution(login_client, owner, facilitator, program, csrf_token):
    page = login_client.get('/admin/')
    assert page.status_code == 200
    assert 'ONPF administration' in page.text
    assert 'Community-program design' not in page.text
    from onpf.administration.service import set_access
    set_access(owner, facilitator.user_id, False)
    assert get_db().execute('SELECT 1 FROM memberships WHERE user_id=?',(facilitator.user_id,)).fetchone()
    from onpf.auth.service import require_role
    with pytest.raises(Exception) as error:
        require_role(facilitator, program['id'], {'facilitator'})
    assert getattr(error.value,'status',None) == 403


@pytest.mark.parametrize('scope', ['system','project','user'])
def test_scoped_ai_off_blocks_direct_generation(app, owner, program, scope):
    from onpf.administration.service import set_ai
    from onpf.drafting.service import generate
    key = '' if scope=='system' else program['id'] if scope=='project' else owner.user_id
    set_ai(owner, scope, key, False)
    app.config['AI_DRAFTING_ENABLED'] = True
    with pytest.raises(Exception) as error:
        generate(owner, program['id'], {'kind':'questions'}, [], '', {})
    assert getattr(error.value, 'code', None) == 'ai_policy_disabled'
    assert scope in str(error.value)


def test_non_admin_cannot_set_system_ai(owner, facilitator, program):
    from onpf.administration.service import set_ai
    with pytest.raises(Exception) as error:
        set_ai(facilitator, 'system', '', False)
    assert getattr(error.value,'status',None) == 403


def test_retirement_withdraws_all_public_links_and_preserves_frozen_source(app,client,owner,program):
    from onpf.publications.service import publish, get_publication
    from onpf.programs.retirement import retire, reactivate
    from onpf.programs.service import get_program, save_document
    frozen=publish(owner,program['id'],program['revision'],reviewed=True)
    digest=frozen['source_hash']
    base=f"/projects/{program['id']}/versions/1"
    assert client.get(base).status_code==200
    retire(owner,program['id'],confirmed=True)
    for path in [base,base+'/documents/overview.html',base+'/documents/overview.odt',base+'/package.zip',base+'/additional/missing/attachment']:
        response=client.get(path)
        assert response.status_code==404
        assert response.headers.get('Cache-Control')=='no-store'
    assert get_db().execute('SELECT source_hash FROM publications WHERE program_id=?',(program['id'],)).fetchone()[0]==digest
    assert get_program(owner,program['id'])['retired_at']
    with pytest.raises(Exception) as error:
        save_document(owner,program['id'],'overview',{'sections':{}},get_program(owner,program['id'])['revision'])
    assert getattr(error.value,'code',None)=='project_retired'
    reactivate(owner,program['id'],confirmed=True)
    assert client.get(base).status_code==404
    live=get_program(owner,program['id'])
    publish(owner,program['id'],live['revision'],reviewed=True)
    assert client.get(f"/projects/{program['id']}/versions/2").status_code==200


def test_general_material_template_family_and_export(owner,program):
    from onpf.additional_documents.service import save_additional,get_additional,public_item
    from onpf.additional_documents.supporting import derive,history
    from onpf.additional_documents.files import document_file
    from onpf.programs.service import get_program
    payload={'template_key':'supporting','title':'Letter template','sections':{},
      'ownership_basis':'own_work','permission_basis':'Project original','license':'MIT-0','notices':'Retain notice',
      'structured':{'version':1,'layout':'letter','brief':'Reusable letters','purpose':'Invite discussion','audience':'Community',
        'blocks':[{'type':'heading','text':'Dear {{name}}'},{'type':'paragraph','text':'Shared wording'},
                  {'type':'response_space','label':'Your comments'},{'type':'checkbox','label':'Please contact me'}]},
      'is_template':True,'reviewed':True}
    template=save_additional(owner,program['id'],payload,program['revision'])
    items=derive(owner,program['id'],template['id'],template['revision'],[{'name':'Chris'},{'name':'Alex'}])
    assert len(items)==2
    assert 'Dear Chris' in json.dumps(items[0]['structured'])
    assert items[0]['template_source_revision']==1
    html=document_file(public_item(items[0]),'html')[0].decode()
    assert 'Your comments' in html and 'Please contact me' in html
    assert b'PK'==document_file(public_item(items[0]),'odt')[0][:2]
    payload['structured']['blocks'][1]['text']='New template wording'
    save_additional(owner,program['id'],payload,get_program(owner,program['id'])['revision'],template['id'])
    assert get_additional(owner,program['id'],items[0]['id'])['structured']['blocks'][1]['text']=='Shared wording'
    assert len(history(owner,program['id'],template['id']))==2


def test_manual_supporting_materials_inert_content_and_batch_limit(owner,program):
    from onpf.additional_documents.service import save_additional,public_item
    from onpf.additional_documents.supporting import derive
    from onpf.additional_documents.files import document_file
    item=save_additional(owner,program['id'],{'template_key':'supporting','title':'Custom survey','sections':{},
      'ownership_basis':'own_work','permission_basis':'Original','license':'MIT-0','notices':'',
      'is_template':True,'reviewed':True,'structured':{'version':1,'layout':'letter','blocks':[{'type':'paragraph','text':'<script>steal()</script>'}]}},program['revision'])
    assert '<script>steal()' not in document_file(public_item(item),'html')[0].decode()
    with pytest.raises(Exception) as error:
        derive(owner,program['id'],item['id'],1,[{}]*21)
    assert getattr(error.value,'code',None)=='material_batch_limit'


def test_supporting_generation_is_unsaved_guarded_and_traceable(app,tmp_path,owner,program):
    from test_ai_drafting import server,source
    from onpf.drafting.service import generate
    from onpf.additional_documents.service import save_additional
    handle=source(owner,program)
    output={'fields':{'title':'Survey draft','structured':{'version':1,'layout':'letter','blocks':[{'type':'response_space','label':'What should we discuss?'}]}},
            'sources':[handle],'uncertainties':['Audience remains unresolved'],'questions':[]}
    with server(app,tmp_path,json.dumps(output)):
        result=generate(owner,program['id'],{'kind':'supporting'},[handle],'One survey',{})
    assert get_db().execute('SELECT COUNT(*) FROM additional_documents').fetchone()[0]==0
    assert 'REVIEW REQUIRED' in result['fields']['structured']['blocks'][0]['review_guard']
    payload={**result['fields'],'template_key':'supporting','sections':{},'ownership_basis':'own_work','permission_basis':'Original','license':'MIT-0','notices':''}
    saved=save_additional(owner,program['id'],payload,program['revision'],ai_receipt=result['receipt'])
    assert saved['reviewed'] is False
    assert get_db().execute('SELECT target_key FROM ai_origins WHERE target_kind="supporting"').fetchone()[0]==saved['id']


def test_general_creation_editor_and_drafting_handoff(login_client,owner,program,csrf_token):
    page=login_client.get(f"/programs/{program['id']}/additional/new")
    assert page.status_code==200 and 'Create supporting material' in page.text
    preview=login_client.post(f"/programs/{program['id']}/drafting",data={
      'csrf_token':csrf_token(login_client),'draft_kind':'supporting','title':'Menu',
      'block_count':'1','block_0_type':'paragraph','block_0_text':'Unknown ingredients',
      'layout':'letter','purpose':'Printable menu'})
    assert preview.status_code==200
    assert 'Unknown ingredients' in preview.text


def test_new_documentation_and_origins_survive_private_restore(app,tmp_path,owner,program):
    from test_ai_drafting import server,source
    from onpf.drafting.organizer import save
    from onpf.drafting.service import generate
    from onpf.additional_documents.service import save_additional
    from onpf.archives.service import backup_private,restore_private
    import sqlite3
    statement=save(owner,program['id'],'Use existing staff',{'kind':'supporting'})
    selected=next(s['handle'] for s in evidence.catalog(owner,program['id'],{'kind':'supporting'}) if s['kind']=='organizer_clarification')
    raw={'fields':{'title':'Sign','structured':{'version':1,'layout':'letter','blocks':[{'type':'paragraph','text':'Unknown local arrangement'}]}},'sources':[selected],'uncertainties':['Unresolved'],'questions':[]}
    with server(app,tmp_path,json.dumps(raw)):
        result=generate(owner,program['id'],{'kind':'supporting'},[selected],'',{})
    save_additional(owner,program['id'],{**result['fields'],'template_key':'supporting','sections':{},'ownership_basis':'own_work','permission_basis':'Original','license':'MIT-0','notices':''},program['revision'],ai_receipt=result['receipt'])
    archive=tmp_path/'private.json'; target=tmp_path/'restored.sqlite3'
    backup_private(app.config['DATABASE'],archive)
    restore_private(archive,target)
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT COUNT(*) FROM ai_origins').fetchone()[0]==1
        assert db.execute('SELECT text FROM organizer_clarifications').fetchone()[0]=='Use existing staff'

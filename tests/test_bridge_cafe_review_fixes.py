"""Reproductions from the single whole-branch review, using fictional data."""
import json
import re
from io import BytesIO

import pytest
from onpf.db import get_db
from onpf.errors import DomainError
from test_bridge_cafe_boundaries import material
from test_ai_routes import hidden, state


def test_public_projection_omits_internal_material_context(owner, program,client):
    from onpf.additional_documents.service import save_additional, public_item
    payload=material()
    payload['structured'].update(brief='PRIVATE donor context',purpose='Private planning',audience='Private staff')
    saved=save_additional(owner,program['id'],payload,program['revision'])
    public=public_item(saved)
    assert 'PRIVATE' not in json.dumps(public)
    assert set(public['structured'])=={'version','layout','blocks'}
    assert saved['structured']['brief']=='PRIVATE donor context'
    from onpf.publications.service import publish
    from onpf.programs.service import get_program
    from zipfile import ZipFile
    payload['include_in_public']=True
    selected=save_additional(owner,program['id'],payload,get_program(owner,program['id'])['revision'])
    publish(owner,program['id'],get_program(owner,program['id'])['revision'],reviewed=True,selected_additional_ids=[selected['id']])
    package=client.get(f"/projects/{program['id']}/versions/1/package.zip")
    assert package.status_code==200
    with ZipFile(BytesIO(package.data)) as archive:
        assert b'PRIVATE donor context' not in archive.read('source/program.json')


def test_retired_project_denies_new_and_legacy_invitation_access(owner, program, batch):
    from onpf.inquiries.service import create_invitation, resolve_invitation
    from onpf.programs.retirement import retire
    token=create_invitation(owner,batch['id'])
    retire(owner,program['id'],confirmed=True)
    with pytest.raises(DomainError):
        create_invitation(owner,batch['id'])
    # Defense in depth even if a historical token has not been marked revoked.
    get_db().execute('UPDATE invitations SET revoked_at=NULL')
    with pytest.raises(DomainError):
        resolve_invitation(token)


def test_changed_selection_blocks_provider_and_preserves_unsaved_text(app,tmp_path,login_client,owner,program):
    from onpf.drafting.organizer import save
    from onpf.drafting import evidence
    from test_ai_drafting import server,suggestion
    first=save(owner,program['id'],'Original reviewed clarification',{'kind':'proposal'})
    path=f"/programs/{program['id']}/drafting"
    page=login_client.get(path+'?kind=proposal')
    handle=next(s['handle'] for s in evidence.catalog(owner,program['id'],{'kind':'proposal'}) if s['kind']=='organizer_clarification')
    save(owner,program['id'],'Changed clarification',{'kind':'proposal'},first['id'],1)
    data={**state(page.text),'action':'generate','handles':handle,
          'current_fields':json.dumps({'title':'Unsaved title','text':'Unsaved text'})}
    with server(app,tmp_path,json.dumps(suggestion('proposal',handle))) as calls:
        denied=login_client.post(path,data=data)
    assert denied.status_code==409
    assert calls==[]
    assert 'Unsaved text' in denied.text and 'changed' in denied.text
    refreshed=login_client.post(path,data={**state(denied.text),'handles':handle,'action':'refresh_sources'})
    assert refreshed.status_code==200
    with server(app,tmp_path,json.dumps(suggestion('proposal',handle))) as calls:
        result=login_client.post(path,data={**state(refreshed.text),'handles':handle,'action':'generate'})
    assert result.status_code==200 and len(calls)==1
    assert 'Changed clarification' in calls[0]['messages'][-1]['content']


def test_historical_evidence_has_one_relation_control(app,tmp_path,login_client,owner,program,submitted):
    from onpf.refinement.service import save_proposal
    from onpf.contributions.service import revise_response
    response_id=submitted['response_ids'][0]
    saved=save_proposal(owner,program['id'],{'title':'Historical option','text':'Keep history','response_ids':[response_id]},None)
    revise_response(owner,response_id,'Corrected response','Fictional correction',1)
    page=login_client.get(f"/programs/{program['id']}/drafting",query_string={'kind':'proposal','record_key':saved['id']})
    assert page.status_code==200
    assert len(re.findall(r'name="current_response_ids" value="'+response_id+'"',page.text))==1
    from onpf.drafting import evidence
    from test_ai_drafting import server,suggestion
    sources=evidence.catalog(owner,program['id'],{'kind':'proposal','record_key':saved['id']})
    historical=next(s['handle'] for s in sources if s['kind']=='response' and '@' in s['handle'] and s['record_key']==response_id)
    path=f"/programs/{program['id']}/drafting"
    output=suggestion('proposal',historical)
    output['fields']['response_ids']=[historical]
    with server(app,tmp_path,json.dumps(output)) as calls:
        generated=login_client.post(path,data={**state(page.text),'handles':historical,'action':'generate',
            'current_editor':'yes','current_title':saved['title'],'current_text':saved['text'],'current_response_ids':response_id})
    assert generated.status_code==200 and len(calls)==1
    applied=login_client.post(path+'/apply',data={**state(generated.text),'replace':['text'],
        'suggested_text':'Reviewed historical wording'})
    assert applied.status_code==200
    saved_page=login_client.post(f"/programs/{program['id']}/refinement/proposals/{saved['id']}",data={
        'csrf_token':hidden(applied.text,'csrf_token'),'expected_revision':hidden(applied.text,'expected_revision'),
        'title':saved['title'],'text':'Reviewed historical wording','response_ids':response_id,
        'ai_receipt':hidden(applied.text,'ai_receipt')})
    assert saved_page.status_code==302, saved_page.text
    origin=json.loads(get_db().execute('SELECT sources FROM ai_origins').fetchone()[0])
    assert origin[0]['handle']==historical


def test_unsaved_material_rights_and_template_status_survive_handoff(app,tmp_path,login_client,owner,program,csrf_token):
    path=f"/programs/{program['id']}/drafting"
    page=login_client.post(path,data={'csrf_token':csrf_token(login_client),'draft_kind':'supporting',
        'title':'Fictional licensed sign','layout':'letter','block_count':'1','block_0_type':'paragraph',
        'block_0_text':'Working content','ownership_basis':'third_party','permission_basis':'Restricted permission',
        'license':'CC BY-NC 4.0','notices':'Fictional attribution','is_template':'yes','include_in_public':'yes'})
    assert page.status_code==200
    metadata=json.loads(hidden(page.text,'destination_state'))
    assert metadata['material']['ownership_basis']=='third_party'
    assert metadata['material']['license']=='CC BY-NC 4.0'
    assert metadata['material']['is_template'] is True
    # Keep editing is the same destination reconstruction used after Apply.
    kept=login_client.post(path,data={**state(page.text),'destination_state':hidden(page.text,'destination_state'),'action':'edit'})
    assert kept.status_code==200
    assert 'Restricted permission' in hidden(kept.text,'destination_state')
    from test_ai_drafting import server,source
    handle=source(owner,program)
    output={'fields':{'title':'Licensed sign','structured':{'version':1,'layout':'letter',
        'blocks':[{'type':'paragraph','text':'Suggested content'}]}},'sources':[handle],'uncertainties':[],'questions':[]}
    with server(app,tmp_path,json.dumps(output)):
        generated=login_client.post(path,data={**state(kept.text),'destination_state':hidden(kept.text,'destination_state'),
            'handles':handle,'action':'generate'})
    assert generated.status_code==200
    applied=login_client.post(path+'/apply',data={**state(generated.text),'destination_state':hidden(generated.text,'destination_state'),
        'replace':['title']})
    assert applied.status_code==200
    assert 'value="third_party" selected' in applied.text
    assert 'CC BY-NC 4.0' in applied.text and 'Restricted permission' in applied.text and 'Fictional attribution' in applied.text
    assert re.search(r'name="is_template"[^>]*checked',applied.text)


@pytest.mark.parametrize('scope',['system','user'])
def test_storage_allowance_aggregates_multiple_projects(owner,program,scope):
    from onpf.programs.service import create_program,get_program
    from onpf.additional_documents.service import save_additional
    from onpf.drafting.usage import set_limits
    saved=save_additional(owner,program['id'],material('Fictional '*100),program['revision'])
    db=get_db()
    used=sum(len(json.dumps(dict(r),ensure_ascii=False).encode()) for r in db.execute('SELECT * FROM additional_documents'))
    used+=sum(len(r[0].encode()) for r in db.execute('SELECT snapshot_json FROM additional_document_revisions'))
    set_limits(owner,scope,'' if scope=='system' else owner.user_id,{'storage_bytes':str(used+2000)})
    other=create_program(owner,{'title':'Second fictional project'})
    with pytest.raises(DomainError) as denied:
        save_additional(owner,other['id'],material('Fictional '*100),other['revision'])
    assert denied.value.code=='material_storage_limit'
    assert db.execute('SELECT COUNT(*) FROM additional_documents').fetchone()[0]==1


def test_derived_image_preserves_exact_attachment(owner,program):
    from PIL import Image
    from onpf.additional_documents.service import save_additional
    from onpf.additional_documents.supporting import derive
    image=BytesIO(); Image.new('RGB',(8,8),'green').save(image,format='PNG')
    payload=material(); payload.update(is_template=True,reviewed=True)
    payload['structured']['blocks']=[{'type':'heading','text':'Hello {{name}}'},{'type':'image','asset':'attachment','text':'Local image'}]
    saved=save_additional(owner,program['id'],payload,program['revision'],upload=image.getvalue(),filename='fictional.png')
    child=derive(owner,program['id'],saved['id'],1,[{'name':'Alex'}])[0]
    assert child['upload_sha256']==saved['upload_sha256']
    assert child['upload_data']==saved['upload_data']
    assert child['template_source_revision']==1


def test_pdf_download_honors_configured_page_allowance(owner,program,login_client):
    from onpf.drafting.usage import set_limits
    from onpf.additional_documents.service import save_additional
    set_limits(owner,'system','',{'pages':'100'})
    payload=material()
    payload['structured']['blocks']=[{'type':'paragraph','text':'Fictional content '*90} for _ in range(90)]
    saved=save_additional(owner,program['id'],payload,program['revision'])
    response=login_client.get(f"/programs/{program['id']}/additional/{saved['id']}.pdf")
    assert response.status_code==200 and response.data.startswith(b'%PDF')


def test_admin_restores_project_creation_permission_control(login_client):
    page=login_client.get('/admin/')
    assert 'Can create projects' in page.text
    assert 'name="action" value="creation"' in page.text


def test_creator_allowance_applies_when_collaborator_edits(owner,facilitator,program):
    from onpf.additional_documents.service import save_additional
    from onpf.drafting.usage import set_limits
    from onpf.programs.service import get_program
    saved=save_additional(owner,program['id'],material(),program['revision'])
    set_limits(owner,'user',owner.user_id,{'storage_bytes':'5000'})
    with pytest.raises(DomainError) as denied:
        save_additional(facilitator,program['id'],material('Additional words '*100),
            get_program(facilitator,program['id'])['revision'],saved['id'])
    assert denied.value.code=='material_storage_limit'
    assert get_db().execute('SELECT revision FROM additional_documents').fetchone()[0]==1


def test_admin_events_record_old_and_new_values(login_client,owner,facilitator,program):
    from onpf.drafting.usage import set_limits
    page=login_client.get('/admin/')
    result=login_client.post('/admin/',data={'csrf_token':hidden(page.text,'csrf_token'),'action':'creation',
        'user_id':facilitator.user_id,'allowed':'no'})
    assert result.status_code==302
    event=json.loads(get_db().execute("SELECT changes_json FROM administrative_events WHERE action='creation'").fetchone()[0])
    assert event=={'old':True,'new':False}
    set_limits(owner,'system','',{'pages':'30'})
    set_limits(owner,'system','',{'pages':'40'})
    event=json.loads(get_db().execute("SELECT changes_json FROM administrative_events WHERE action='ai_limits' ORDER BY id DESC").fetchone()[0])
    assert event['old']['pages']==30 and event['new']['pages']==40


@pytest.mark.parametrize('damage',['missing','tampered','wrong_target'])
def test_reviewed_selection_state_is_required_and_scope_bound(app,tmp_path,login_client,owner,program,damage):
    from test_ai_drafting import server,source,suggestion
    path=f"/programs/{program['id']}/drafting"
    page=login_client.get(path+'?kind=proposal')
    data={**state(page.text),'handles':source(owner,program),'action':'generate'}
    if damage=='missing':
        data.pop('evidence_state')
    elif damage=='tampered':
        data['evidence_state']=data['evidence_state'][:-1]+'!'
    else:
        data['target']='{"kind":"questions"}'
        data['current_fields']='{}'
    with server(app,tmp_path,json.dumps(suggestion('proposal',source(owner,program)))) as calls:
        denied=login_client.post(path,data=data)
    assert denied.status_code==409 and calls==[]

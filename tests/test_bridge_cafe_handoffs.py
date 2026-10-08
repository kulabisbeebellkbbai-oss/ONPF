import json,re
from onpf.db import get_db


def test_saved_custom_question_reopens_full_context(login_client,owner,program,csrf_token):
    from onpf.inquiries.service import save_question
    question=save_question(owner,program['id'],{'text':'Distinctive saved question?','stage':2,
        'document_key':'overview','answer_type':'text','why':'Original rationale','group_label':'Kitchen',
        'source_type':'program','source_id':program['id']},program['revision'])
    page=login_client.post(f"/programs/{program['id']}/drafting",data={
        'csrf_token':csrf_token(login_client),'draft_kind':'questions','draft_record_key':question['id']})
    assert page.status_code==200
    assert re.search(r'name="current_q0_reason"[^>]*>Original rationale</textarea>',page.text)
    assert re.search(r'name="current_q0_source_handles"[^>]* checked',page.text)
    assert 'Kitchen' in page.text
    assert get_db().execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0]==0


def test_blank_native_material_editor_receives_review_guard(app,tmp_path,owner,program):
    from test_ai_drafting import server,source
    from onpf.additional_documents.editor import blank_structure
    from onpf.drafting.service import generate
    handle=source(owner,program)
    raw={'fields':{'title':'Fictional sign','structured':{'version':1,'layout':'letter',
         'blocks':[{'type':'paragraph','text':'Unreviewed initial content'}]}},
         'sources':[handle],'uncertainties':['Local details unresolved'],'questions':[]}
    with server(app,tmp_path,json.dumps(raw)):
        result=generate(owner,program['id'],{'kind':'supporting'},[handle],'',
                        {'title':'Fictional sign','structured':blank_structure()})
    assert 'REVIEW REQUIRED' in result['fields']['structured']['blocks'][0]['review_guard']

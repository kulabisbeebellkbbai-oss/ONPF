"""Cross-request acceptance boundaries on fictional local data."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from onpf.db import get_db
from onpf.errors import DomainError


def material(text='Working wording'):
    return {'template_key':'supporting','title':'Fictional form','sections':{},
            'ownership_basis':'own_work','permission_basis':'Original','license':'MIT-0',
            'notices':'','structured':{'version':1,'layout':'letter',
            'blocks':[{'type':'paragraph','text':text}]}}


def test_guarded_cleanup_cannot_empty_or_dismiss_a_section():
    from onpf.drafting.guards import GUARD, protect_sections
    prior={'sections':{'purpose':GUARD+'\n\nUnreviewed purpose'}}
    result=protect_sections({'sections':{'purpose':''}},prior)
    assert result['sections']['purpose']==prior['sections']['purpose']


def test_save_enforces_scoped_page_limit_and_rolls_back(owner,program):
    from onpf.additional_documents.service import save_additional
    from onpf.drafting.usage import set_limits
    set_limits(owner,'project',program['id'],{'pages':'1'})
    payload=material()
    payload['structured']['blocks']=[{'type':'paragraph','text':'Fictional wording '*70} for _ in range(12)]
    with pytest.raises(DomainError) as denied:
        save_additional(owner,program['id'],payload,program['revision'])
    assert denied.value.code=='material_page_limit'
    assert get_db().execute('SELECT COUNT(*) FROM additional_documents').fetchone()[0]==0


def test_storage_limit_includes_retained_revision_content(owner,program):
    from onpf.additional_documents.service import save_additional
    from onpf.drafting.usage import set_limits
    from onpf.programs.service import get_program
    payload=material('Fictional text '*100)
    saved=save_additional(owner,program['id'],payload,program['revision'])
    db=get_db()
    retained=sum(len(row[0].encode()) for row in db.execute('SELECT snapshot_json FROM additional_document_revisions'))
    current=len(json.dumps(dict(db.execute('SELECT * FROM additional_documents').fetchone()),ensure_ascii=False).encode())
    set_limits(owner,'project',program['id'],{'storage_bytes':str(current+retained+10)})
    payload['structured']['blocks'][0]['text']='Corrected text '*100
    with pytest.raises(DomainError) as denied:
        save_additional(owner,program['id'],payload,get_program(owner,program['id'])['revision'],saved['id'])
    assert denied.value.code=='material_storage_limit'
    assert db.execute('SELECT revision FROM additional_documents').fetchone()[0]==1


def test_rationale_editor_synchronizes_legacy_metadata():
    from werkzeug.datastructures import MultiDict
    from onpf.drafting.routes import native_fields
    fields=native_fields({'kind':'questions'},MultiDict({
        'question_count':'1','q0_text':'Distinctive question?','q0_stage':'2',
        'q0_document_key':'overview','q0_reason':'Corrected intent','q0_why':'Prior intent',
        'q0_group_label':'Kitchen','q0_source_type':'program','q0_source_id':'fictional'}))
    assert fields['questions'][0]['why']=='Corrected intent'


def test_legacy_management_entry_redirects_to_separate_app(login_client,csrf_token):
    result=login_client.get('/manage')
    assert result.status_code==302 and result.location=='/admin/'
    result=login_client.post('/manage',data={'csrf_token':csrf_token(login_client),'action':'create_user'})
    assert result.status_code==308 and result.location=='/admin/'


def test_simultaneous_reservations_cannot_overspend(app,owner,program):
    from onpf.drafting import usage
    app.config['AI_DRAFTING_ENABLED']=True
    usage.set_limits(owner,'system','',{'daily_budget_micro':'100','input_micro_per_1000':'1000',
                                     'output_micro_per_1000':'1000','priced_model':'onpf-drafting'})
    barrier=Barrier(2)
    def attempt():
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                return usage.reserve(owner,program['id'],40,40,40)
            except DomainError as error:
                return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert len([r for r in results if r=='ai_concurrency_limit'])==1
    assert get_db().execute('SELECT COUNT(*),SUM(charged_micro) FROM ai_usage').fetchone()[:]==(1,80)

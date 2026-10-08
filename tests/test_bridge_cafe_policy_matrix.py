import itertools,json
import pytest
from onpf.errors import DomainError
from onpf.db import get_db


@pytest.mark.parametrize('switches',list(itertools.product((False,True),repeat=3)))
def test_all_scope_switch_combinations(app,owner,program,switches):
    from onpf.administration.service import set_ai,effective,require_ai
    app.config['AI_DRAFTING_ENABLED']=True
    for (scope,target),enabled in zip([('system',''),('project',program['id']),('user',owner.user_id)],switches):
        set_ai(owner,scope,target,enabled)
    assert effective(owner,program['id'])['enabled']==all(switches)
    if all(switches):
        assert require_ai(owner,program['id'])['enabled']
    else:
        with pytest.raises(DomainError,match='off'):
            require_ai(owner,program['id'])
    assert get_db().execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0]==0


def test_unknown_price_monetary_cap_makes_zero_provider_requests(app,tmp_path,owner,program):
    from test_ai_drafting import server,source,suggestion
    from onpf.drafting.usage import set_limits
    from onpf.drafting.service import generate
    from onpf.programs.service import save_document
    set_limits(owner,'system','',{'daily_budget_micro':'100'})
    handle=source(owner,program)
    with server(app,tmp_path,json.dumps(suggestion('proposal',handle))) as calls:
        with pytest.raises(DomainError) as denied:
            generate(owner,program['id'],{'kind':'proposal'},[handle],'',{})
        assert denied.value.code=='ai_pricing_unknown'
        assert calls==[]
        # The monetary denial does not prevent ordinary manual draft saving.
        saved=save_document(owner,program['id'],'overview',{'sections':{'purpose':'Manual fictional draft'}},program['revision'])
        assert saved['documents']['overview']['sections']['purpose']=='Manual fictional draft'
    assert get_db().execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0]==0

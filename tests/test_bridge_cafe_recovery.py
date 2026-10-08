"""Recover retired projects, scoped policy and administrative audit history."""
import json
import pytest
from onpf.db import get_db,connect
from onpf.errors import DomainError


def test_retirement_and_administration_roundtrip(app,tmp_path,owner,program):
    from onpf.archives.service import backup_private,restore_private
    from onpf.administration.service import set_ai
    from onpf.drafting.usage import set_limits
    from onpf.programs.retirement import retire
    for scope,key in [('system',''),('project',program['id']),('user',owner.user_id)]:
        set_ai(owner,scope,key,False)
    set_limits(owner,'system','',{'daily_budget_micro':'100','priced_model':'onpf-drafting',
        'input_micro_per_1000':'1000','output_micro_per_1000':'1000'})
    retire(owner,program['id'],confirmed=True)
    expected={table:[dict(r) for r in get_db().execute(f'SELECT * FROM {table} ORDER BY rowid')]
        for table in ('programs','ai_policies','administrative_events','project_retirement_events')}
    backup=tmp_path/'private.json'
    backup_private(app.config['DATABASE'],backup)
    destination=tmp_path/'restored.sqlite3'
    restore_private(backup,destination)
    db=connect(destination)
    try:
        for table,rows in expected.items():
            assert [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]==rows
    finally:
        db.close()


@pytest.mark.parametrize('damage',['scope','target','limit','event_id','malformed_json'])
def test_control_archive_rejects_invalid_records_before_restore(app,tmp_path,owner,program,damage):
    from onpf.archives.service import backup_private,restore_private
    from onpf.archives.validation import digest
    from onpf.drafting.usage import set_limits
    set_limits(owner,'system','',{'pages':'40'})
    backup=tmp_path/'private.json'; backup_private(app.config['DATABASE'],backup)
    value=json.loads(backup.read_text(encoding='utf8'))
    if damage=='scope':
        value['tables']['ai_policies'][0]['scope']='unknown'
    elif damage=='target':
        value['tables']['ai_policies'][0]['target_id']=program['id']
    elif damage=='limit':
        value['tables']['ai_policies'][0]['limits_json']='{"pages":100000}'
    elif damage=='event_id':
        value['tables']['administrative_events'][0]['id']=-1
    else:
        value['tables']['ai_policies'][0]['limits_json']='{bad'
    value['hashes']={table:digest(rows) for table,rows in value['tables'].items()}
    value['archive_hash']=digest({key:item for key,item in value.items() if key!='archive_hash'})
    backup.write_text(json.dumps(value),encoding='utf8')
    destination=tmp_path/'refused.sqlite3'
    with pytest.raises(DomainError) as denied:
        restore_private(backup,destination)
    assert denied.value.code=='invalid_backup'
    assert not destination.exists()

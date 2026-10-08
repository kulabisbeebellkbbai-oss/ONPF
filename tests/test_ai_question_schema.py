"""The real HTTP boundary must request all question keys from the provider."""
import json

import pytest

from test_ai_drafting import drafting, server, snapshot, source, suggestion


@pytest.mark.parametrize('target', [{'kind':'questions'},
    {'kind':'questions','stage':3,'document_key':'delivery'}])
def test_question_generation_requests_strict_complete_schema(drafting, app, tmp_path, owner, program, target):
    # JSON mode alone permits key omissions; removing the strict schema must fail.
    handle = source(owner, program)
    value = suggestion('questions',handle,document_key=target.get('document_key','overview'))
    value['fields']['questions'][0]['stage'] = target.get('stage',1)
    before = snapshot()
    with server(app,tmp_path,json.dumps(value)) as requests:
        result = drafting.generate(owner,program['id'],target,[handle],'Preserve wording.',{})
    assert result['fields'] == value['fields']
    assert snapshot() == before
    response_format = requests[0]['response_format']
    assert response_format['type'] == 'json_schema'
    assert response_format['json_schema']['strict'] is True
    schema = response_format['json_schema']['schema']
    assert set(schema['required']) == {'fields','sources','uncertainties','questions'}
    draft = schema['properties']['fields']['properties']['questions']['items']
    assert set(draft['required']) == {'text','stage','document_key','answer_type','reason','source_handles'}
    assert draft['properties']['answer_type'] == {'type':'string','enum':['text']}
    assert draft['properties']['source_handles']['type'] == 'array'
    assert draft['properties']['stage']['enum'] == ([3] if 'stage' in target else [1,2,3,4,5,6,7])
    if 'document_key' in target:
        assert draft['properties']['document_key']['enum'] == ['delivery']
    followup = schema['properties']['questions']['items']
    assert set(followup['required']) == {'text','stage','document_key','reason','source_handles'}
    assert 'answer_type' not in followup['properties']
    assert followup['properties']['stage']['enum'] == [1,2,3,4,5,6,7]
    def require_closed_objects(node):
        if isinstance(node,dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for child in node.values(): require_closed_objects(child)
        elif isinstance(node,list):
            for child in node: require_closed_objects(child)
    require_closed_objects(schema)


def test_strict_schema_does_not_replace_local_missing_support_validation(drafting, app, tmp_path, owner, program):
    from onpf.errors import DomainError
    handle = source(owner,program)
    value = suggestion('questions',handle)
    del value['fields']['questions'][0]['source_handles']
    with server(app,tmp_path,json.dumps(value)):
        with pytest.raises(DomainError) as denied:
            drafting.generate(owner,program['id'],{'kind':'questions'},[handle],'',{})
    assert denied.value.missing_fields == ('fields.questions[].source_handles',)

"""Rejections identify fixed contract failures without disclosing provider data."""
import json

import pytest

from onpf.drafting.contracts import validate_output
from onpf.errors import DomainError
from test_ai_drafting import server, snapshot, source, suggestion
from test_ai_routes import hidden


@pytest.mark.parametrize('location,key,expected', [
    ('envelope', 'fields', 'fields'),
    ('envelope', 'questions', 'questions'),
    ('fields', 'questions', 'fields.questions'),
    ('draft_question', 'reason', 'fields.questions[].reason'),
    ('draft_question', 'source_handles', 'fields.questions[].source_handles'),
    ('followup', 'reason', 'questions[].reason'),
    ('followup', 'source_handles', 'questions[].source_handles'),
])
def test_missing_required_field_identifies_schema_path_only(app_context, location, key, expected, caplog):
    # Catch ambiguous rejection diagnostics without exposing private values.
    handle = 'response:fictional-program:fictional-response'
    value = suggestion('questions', handle)
    value['uncertainties'] = ['PRIVATE-PROVIDER-SENTINEL']
    objects = {'envelope': value, 'fields': value['fields'],
        'draft_question': value['fields']['questions'][0], 'followup': value['questions'][0]}
    del objects[location][key]
    with pytest.raises(DomainError) as denied:
        validate_output({'kind': 'questions'}, json.dumps(value), {handle})
    assert denied.value.missing_fields == (expected,)
    assert 'Missing required fields: ' + expected + '.' in str(denied.value)
    assert 'PRIVATE-PROVIDER-SENTINEL' not in str(denied.value) + caplog.text
    assert handle not in str(denied.value)


@pytest.mark.parametrize('change,reason', [
    ('missing_answer_type', 'question_missing_answer_type'),
    ('followup_answer_type', 'followup_unexpected_answer_type'),
    ('unknown_key', 'unexpected_fields'),
    ('missing_key', 'missing_required_fields'),
    ('stage', 'invalid_question_stage'),
    ('document', 'invalid_question_document'),
    ('source', 'unselected_source'),
    ('duplicate_source', 'duplicate_source'),
    ('undeclared_source', 'undeclared_citation'),
    ('question_limit', 'question_limit_exceeded'),
    ('uncertainty_limit', 'uncertainty_limit_exceeded'),
    ('malformed', 'invalid_json'),
    ('duplicate_key', 'duplicate_json_key'),
    ('nonfinite', 'nonfinite_json_value'),
    ('oversize', 'output_byte_limit'),
    ('wrong_type', 'invalid_text_type'),
])
def test_output_rejection_reports_only_fixed_reason(app_context, change, reason, caplog):
    handle = 'response:fictional-program:fictional-response'
    second = 'response:fictional-program:second-response'
    value = suggestion('questions', handle)
    question = value['fields']['questions'][0]
    private = 'PRIVATE-PROVIDER-SENTINEL'
    if change == 'missing_answer_type': del question['answer_type']
    elif change == 'followup_answer_type': value['questions'][0]['answer_type'] = private
    elif change == 'unknown_key': question[private] = private
    elif change == 'missing_key': del value['fields']
    elif change == 'stage': question['stage'] = private
    elif change == 'document': question['document_key'] = private
    elif change == 'source': question['source_handles'] = [private]
    elif change == 'duplicate_source': value['sources'] *= 2
    elif change == 'undeclared_source': question['source_handles'] = [second]
    elif change == 'question_limit': value['fields']['questions'] *= 21
    elif change == 'uncertainty_limit': value['uncertainties'] *= 21
    elif change == 'wrong_type': question['text'] = {private: private}
    raw = json.dumps(value)
    if change == 'malformed': raw = private
    elif change == 'duplicate_key': raw = raw[:-1] + ',"fields":{}}'
    elif change == 'nonfinite': raw = raw.replace('"stage": 1', '"stage": NaN', 1)
    elif change == 'oversize': raw += private * 65536
    with pytest.raises(DomainError) as denied:
        validate_output({'kind': 'questions'}, raw, {handle, second})
    assert denied.value.code == 'invalid_ai_output'
    assert denied.value.status == 502
    assert denied.value.reason_code == reason
    assert f'Validation reason: {reason}.' in str(denied.value)
    assert private not in str(denied.value)
    assert private not in caplog.text
    assert handle not in str(denied.value)


@pytest.mark.parametrize('change,reason', [
    ('missing_answer_type', 'question_missing_answer_type'),
    ('unknown_key', 'unexpected_fields'),
    ('missing_support_key', 'missing_required_fields'),
])
def test_browser_rejection_preserves_questions_and_discloses_only_reason(
        app, tmp_path, login_client, owner, program, caplog, change, reason):
    path = f'/programs/{program["id"]}/drafting'
    page = login_client.get(path + '?kind=questions')
    handle = source(owner, program)
    value = suggestion('questions', handle)
    private = 'PRIVATE-PROVIDER-SENTINEL'
    if change == 'missing_answer_type':
        del value['fields']['questions'][0]['answer_type']
    elif change == 'missing_support_key':
        del value['fields']['questions'][0]['source_handles']
    else:
        value['fields']['questions'][0][private] = private
    current = {'questions': [{'text': 'Keep this approved question?', 'stage': 1,
        'document_key': 'overview', 'answer_type': 'text', 'reason': 'Keep this reason.',
        'source_handles': []}]}
    before = snapshot()
    with server(app, tmp_path, json.dumps(value)):
        rejected = login_client.post(path, data={'csrf_token': hidden(page.text, 'csrf_token'), 'evidence_state': hidden(page.text, 'evidence_state'),
            'target': '{"kind":"questions"}', 'current_fields': json.dumps(current),
            'handles': handle, 'action': 'generate', 'instructions': 'Keep approved wording.'})
    assert rejected.status_code == 502
    assert f'Validation reason: {reason}.' in rejected.text
    if change == 'missing_support_key':
        assert 'Missing required fields: fields.questions[].source_handles.' in rejected.text
    assert 'Keep this approved question?' in rejected.text
    assert 'Keep this reason.' in rejected.text
    assert 'Keep approved wording.' in rejected.text
    assert private not in rejected.text and private not in caplog.text
    assert snapshot() == before

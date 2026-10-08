"""Fictional shared materials exercise immutable versions and private drafts."""
import json
import re
import sqlite3

import pytest
from werkzeug.datastructures import MultiDict

from onpf.auth.models import Principal
from onpf.db import get_db
from onpf.errors import DomainError
from onpf.programs.service import create_program, get_program


def materials():
    from onpf.materials import service
    return service


def payload(text='Fictional first version', **changes):
    return {'title': 'Fictional shared guide', 'content': {'schema_version': 1, 'kind': 'text', 'text': text},
            'ownership_basis': 'own_work', 'permission_basis': 'Fictional program authors own this text',
            'license': 'MIT', 'notices': 'Copyright Fictional authors', **changes}


def released(owner, program, **changes):
    draft = materials().save_material(owner, program['id'], payload(**changes), None)
    return draft, materials().release_material(owner, draft['id'], draft['revision'])


def test_updating_shared_source_does_not_update_adopters(owner, other_owner, program):
    service = materials()
    draft, v1 = released(owner, program)
    adopter = create_program(other_owner, {'title': 'Fictional adopter'})
    service.adopt_material(other_owner, adopter['id'], v1['id'], adopter['revision'])
    updated = service.save_material(owner, program['id'], payload('Fictional second version', id=draft['id']), draft['revision'])
    v2 = service.release_material(owner, updated['id'], updated['revision'])
    assert service.list_adoptions(other_owner, adopter['id'])[0]['version_id'] == v1['id']
    assert service.get_version(other_owner, v1['id'])['content']['text'] == 'Fictional first version'
    assert v2['version_number'] == 2
    assert v1['content_hash'] != v2['content_hash']
    service.adopt_material(other_owner, adopter['id'], v2['id'], get_program(other_owner, adopter['id'])['revision'])
    assert service.list_adoptions(other_owner, adopter['id'])[0]['version_id'] == v2['id']
    assert service.list_adoptions(owner, program['id']) == []


def test_local_derivative_retains_source(owner, other_owner, program):
    service = materials()
    _, v1 = released(owner, program)
    adopter = create_program(other_owner, {'title': 'Fictional adopter'})
    derivative = service.derive_material(other_owner, adopter['id'], v1['id'])
    assert derivative['program_id'] == adopter['id']
    assert derivative['source_version_id'] == v1['id']
    derivative = service.save_material(other_owner, adopter['id'], payload('Fictional local adaptation', id=derivative['id']), derivative['revision'])
    local_version = service.release_material(other_owner, derivative['id'], derivative['revision'])
    assert local_version['source_version_id'] == v1['id']
    assert service.get_version(owner, v1['id'])['content']['text'] == 'Fictional first version'


def test_cross_owner_material_mutation_denied(owner, other_owner, program):
    service = materials()
    draft, version = released(owner, program)
    other = create_program(other_owner, {'title': 'Fictional other'})
    for action in [lambda: service.save_material(other_owner, program['id'], payload(id=draft['id']), 1),
                   lambda: service.save_material(other_owner, other['id'], payload(id=draft['id']), 1),
                   lambda: service.release_material(other_owner, draft['id'], 1)]:
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.status == 403
    assert service.get_version(other_owner, version['id'])['id'] == version['id']


def test_drafts_private_and_versions_designer_only(owner, other_owner, program, facilitator):
    service = materials()
    draft, version = released(owner, program)
    assert service.get_material(facilitator, program['id'], draft['id'])['id'] == draft['id']
    for actor in [other_owner, Principal(None, None, None), Principal(None, 'invite', 'batch')]:
        with pytest.raises(DomainError) as error:
            service.get_material(actor, program['id'], draft['id'])
        assert error.value.status == 403
        with pytest.raises(DomainError) as error:
            service.get_version(actor, version['id'])
        assert error.value.status == 403


def test_facilitator_drafts_but_owner_releases_and_adopts(owner, facilitator, program):
    service = materials()
    draft = service.save_material(facilitator, program['id'], payload(), None)
    with pytest.raises(DomainError) as error:
        service.release_material(facilitator, draft['id'], draft['revision'])
    assert error.value.status == 403
    version = service.release_material(owner, draft['id'], draft['revision'])
    with pytest.raises(DomainError) as error:
        service.adopt_material(facilitator, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    assert error.value.status == 403


def test_stale_material_and_adoption_are_atomic(owner, program):
    service = materials()
    draft, version = released(owner, program)
    revision = get_program(owner, program['id'])['revision']
    for action in [lambda: service.save_material(owner, program['id'], payload('Wrong', id=draft['id']), 0),
                   lambda: service.release_material(owner, draft['id'], 0),
                   lambda: service.adopt_material(owner, program['id'], version['id'], revision - 1)]:
        with pytest.raises(DomainError) as error:
            action()
        assert error.value.status == 409
    assert service.get_material(owner, program['id'], draft['id'])['content']['text'] == 'Fictional first version'
    assert len(service.list_versions(owner, draft['id'])) == 1
    assert service.list_adoptions(owner, program['id']) == []
    assert get_program(owner, program['id'])['revision'] == revision


def test_draft_and_adoption_edits_increment_program_revision(owner, program):
    service = materials()
    start = get_program(owner, program['id'])['revision']
    draft, version = released(owner, program)
    assert get_program(owner, program['id'])['revision'] == start + 1
    service.adopt_material(owner, program['id'], version['id'], start + 1)
    assert get_program(owner, program['id'])['revision'] == start + 2
    service.derive_material(owner, program['id'], version['id'])
    assert get_program(owner, program['id'])['revision'] == start + 3


def test_versions_immutable_at_database_boundary(owner, program):
    _, version = released(owner, program)
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('UPDATE material_versions SET content=? WHERE id=?', ('{}', version['id']))
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('DELETE FROM material_versions WHERE id=?', (version['id'],))


def test_third_party_basis_and_notices_survive_release_and_derivation(owner, other_owner, program):
    service = materials()
    _, version = released(owner, program, ownership_basis='third_party', permission_basis='Fictional written permission permits adaptation', license='Fictional licensed terms', notices='Fictional attribution must remain')
    adopter = create_program(other_owner, {'title': 'Fictional adopter'})
    derivative = service.derive_material(other_owner, adopter['id'], version['id'])
    for key in ('ownership_basis', 'permission_basis', 'license', 'notices'):
        assert derivative[key] == version[key]
    derivative = service.save_material(other_owner, adopter['id'], payload('Local', id=derivative['id'], ownership_basis=derivative['ownership_basis'], permission_basis=derivative['permission_basis'], license=derivative['license'], notices=derivative['notices']), 1)
    local = service.release_material(other_owner, derivative['id'], 2)
    assert local['notices'] == 'Fictional attribution must remain'
    assert local['license'] == 'Fictional licensed terms'


@pytest.mark.parametrize('changes', [ {'permission_basis': ''}, {'ownership_basis': 'unknown'}, {'license': ''},
    {'content': {'schema_version': 1, 'kind': 'text', 'text': 'Fictional', 'private_owner_ids': ['x']}},
    {'content': {'schema_version': 2, 'kind': 'text', 'text': 'Fictional'}},
    {'content': {'schema_version': 1, 'kind': 'text', 'text': []}} ])
def test_invalid_material_does_not_write(owner, program, changes):
    with pytest.raises(DomainError) as error:
        materials().save_material(owner, program['id'], payload(**changes), None)
    assert error.value.status == 422
    assert materials().list_materials(owner, program['id']) == []
    assert get_program(owner, program['id'])['revision'] == program['revision']


def test_document_bundle_is_editable_content_without_private_fields(owner, program):
    content = {'schema_version': 1, 'kind': 'document_bundle', 'documents': {'delivery': {
        'title': 'Fictional guided art delivery', 'sections': [{'key': 'activities', 'label': 'Activities', 'text': 'Fictional painting'}]},
        'budget': {'title': 'Fictional budget', 'sections': [], 'rows': [{'item': 'Fictional paint', 'quantity': '1', 'unit_cost': None, 'cost_status': 'estimated', 'notes': ''}]}}}
    draft, version = released(owner, program, content=content)
    assert version['content'] == content
    content['documents']['delivery']['decisions'] = ['private']
    with pytest.raises(DomainError) as error:
        materials().save_material(owner, program['id'], payload(id=draft['id'], content=content), 1)
    assert error.value.status == 422


def test_derivative_cannot_rewrite_lineage_or_drop_source_notices(owner, program):
    service = materials()
    _, version = released(owner, program)
    derivative = service.derive_material(owner, program['id'], version['id'])
    for changes in [{'source_version_id': None}, {'notices': ''}]:
        with pytest.raises(DomainError) as error:
            service.save_material(owner, program['id'], payload(id=derivative['id'], **changes), 1)
        assert error.value.status == 422


def test_adaptation_can_describe_local_terms_while_original_terms_remain(owner, program):
    service = materials()
    _, source = released(owner, program)
    derivative = service.derive_material(owner, program['id'], source['id'])
    saved = service.save_material(owner, program['id'], payload('Fictional proprietary local changes', id=derivative['id'],
        ownership_basis='third_party', permission_basis='Fictional permission for local added work',
        license='Fictional local adaptation terms', notices='Copyright Fictional authors\nFictional adaptation notice'), 1)
    version = service.release_material(owner, saved['id'], 2)
    assert version['license'] == 'Fictional local adaptation terms'
    assert version['source_version_id'] == source['id']
    original = service.get_version(owner, source['id'])
    assert original['license'] == 'MIT'
    assert original['permission_basis'] == 'Fictional program authors own this text'
    assert original['notices'] == 'Copyright Fictional authors'
    assert version['source_terms'][0]['license'] == 'MIT'
    assert version['source_terms'][0]['notices'] == 'Copyright Fictional authors'


def test_improvement_is_internal_proposal_with_explicit_frozen_content(owner, other_owner, facilitator, program):
    service = materials()
    draft, version = released(owner, program)
    adopter = create_program(other_owner, {'title': 'Fictional adopter'})
    derivative = service.derive_material(other_owner, adopter['id'], version['id'])
    derivative = service.save_material(other_owner, adopter['id'], payload('Fictional proposed adaptation', id=derivative['id']), 1)
    proposal = service.propose_improvement(other_owner, adopter['id'], derivative['id'], 'Fictional improvement rationale', 2, share_content=True)
    service.save_material(other_owner, adopter['id'], payload('Private later draft', id=derivative['id']), 2)
    received = service.list_improvements(owner, program['id'])[0]
    assert received['id'] == proposal['id']
    assert received['proposed_content']['text'] == 'Fictional proposed adaptation'
    assert received['derivative_id'] == derivative['id']
    assert service.get_material(owner, program['id'], draft['id'])['content']['text'] == 'Fictional first version'
    with pytest.raises(DomainError) as error:
        service.get_material(owner, adopter['id'], derivative['id'])
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        service.list_improvements(facilitator, program['id'])
    assert error.value.status == 403


def test_improvement_without_content_does_not_disclose_draft(owner, other_owner, program):
    _, version = released(owner, program)
    adopter = create_program(other_owner, {'title': 'Fictional adopter'})
    derivative = materials().derive_material(other_owner, adopter['id'], version['id'])
    materials().propose_improvement(other_owner, adopter['id'], derivative['id'], 'Fictional note only', 1)
    assert materials().list_improvements(owner, program['id'])[0]['proposed_content'] is None


def test_compare_returns_selected_versions_and_literal_diff(owner, program):
    draft, v1 = released(owner, program)
    draft = materials().save_material(owner, program['id'], payload('Fictional changed text', id=draft['id']), 1)
    v2 = materials().release_material(owner, draft['id'], 2)
    comparison = materials().compare_versions(owner, v1['id'], v2['id'])
    assert comparison['left']['id'] == v1['id']
    assert comparison['right']['id'] == v2['id']
    assert 'Earlier: Fictional first version' in comparison['diff']
    assert 'Later: Fictional changed text' in comparison['diff']


def test_browser_library_release_adopt_derive_and_compare(login_client, owner, program, csrf_token):
    service = materials()
    client = login_client
    path = f"/programs/{program['id']}/materials"
    assert client.get(f"/programs/{program['id']}").status_code == 200
    assert path in client.get(f"/programs/{program['id']}").text
    new_path = path + '/new'
    token = csrf_token(client, new_path)
    result = client.post(new_path, data={**payload(), 'content': json.dumps(payload()['content']), 'csrf_token': token})
    assert result.status_code == 302
    draft = service.list_materials(owner, program['id'])[0]
    assert client.post(path + f"/{draft['id']}/release", data={'expected_revision': 1, 'csrf_token': token}).status_code == 302
    version = service.list_versions(owner, draft['id'])[0]
    result = client.post(path + '/adopt', data={'version_id': version['id'], 'expected_revision': get_program(owner, program['id'])['revision'], 'csrf_token': token})
    assert result.status_code == 302
    assert service.list_adoptions(owner, program['id'])[0]['version_id'] == version['id']
    assert client.post(path + '/derive', data={'version_id': version['id'], 'csrf_token': token}).status_code == 302
    comparison = client.get(path + f"/compare?left={version['id']}&right={version['id']}")
    assert comparison.status_code == 200
    assert 'Fictional first version' in comparison.text


def test_browser_invalid_and_stale_edits_preserve_text_and_escape(login_client, owner, program, csrf_token):
    draft, version = released(owner, program)
    path = f"/programs/{program['id']}/materials/{draft['id']}"
    token = csrf_token(login_client, path)
    text = '<script>Fictional private text</script>'
    result = login_client.post(path, data={**payload(text), 'content': json.dumps(payload(text)['content']), 'expected_revision': 0, 'csrf_token': token})
    assert result.status_code == 409
    assert '&lt;script&gt;' in result.text and '<script>Fictional' not in result.text
    assert service_text(owner, program, draft) == 'Fictional first version'
    result = login_client.post(path, data={**payload(), 'content': '{Fictional malformed JSON', 'expected_revision': 1, 'csrf_token': token})
    assert result.status_code == 422
    assert '{Fictional malformed JSON' in result.text
    assert result.headers['Cache-Control'] == 'no-store'


def service_text(owner, program, draft):
    return materials().get_material(owner, program['id'], draft['id'])['content']['text']


def test_browser_csrf_and_foreign_drafts_denied(login_client, other_owner, program):
    other = create_program(other_owner, {'title': 'Fictional foreign'})
    draft, _ = released(other_owner, other)
    path = f"/programs/{program['id']}/materials"
    assert login_client.post(path + '/new', data=payload()).status_code == 400
    assert login_client.get(path + f"/{draft['id']}").status_code == 403


def test_browser_malformed_bundle_is_preserved_without_renderer_crash(login_client, owner, program, csrf_token):
    path = f"/programs/{program['id']}/materials/new"
    content = {'schema_version': 1, 'kind': 'document_bundle', 'documents': ['Fictional malformed content']}
    token = csrf_token(login_client, path)
    response = login_client.post(path, data={**payload(content=content), 'content': json.dumps(content), 'csrf_token': token})
    assert response.status_code == 422
    assert 'Fictional malformed content' in response.text
    assert materials().list_materials(owner, program['id']) == []


def test_browser_document_adaptation_edits_named_fields(login_client, owner, program, csrf_token):
    content = {'schema_version': 1, 'kind': 'document_bundle', 'documents': {'delivery': {
        'title': 'Fictional art delivery', 'sections': [{'key': 'activities', 'label': 'Activities', 'text': 'Fictional painting'}]}}}
    _, version = released(owner, program, content=content)
    derivative = materials().derive_material(owner, program['id'], version['id'])
    path = f"/programs/{program['id']}/materials/{derivative['id']}"
    token = csrf_token(login_client, path)
    section_name = next(name for name, original in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', login_client.get(path).text, re.S) if original == 'Fictional painting')
    response = login_client.post(path, data={key: value for key, value in {
        **payload(), 'content': None, 'title_delivery': 'Fictional local art delivery',
        section_name: '<script>Fictional local painting</script>', 'expected_revision': 1, 'csrf_token': token}.items() if value is not None})
    assert response.status_code == 302
    saved = materials().get_material(owner, program['id'], derivative['id'])
    assert saved['content']['documents']['delivery']['title'] == 'Fictional local art delivery'
    assert saved['content']['documents']['delivery']['sections'][0]['text'] == '<script>Fictional local painting</script>'
    assert '&lt;script&gt;' in login_client.get(path).text


def test_browser_distinct_section_keys_preserve_both_edits(login_client, owner, program, csrf_token):
    content = {'schema_version': 1, 'kind': 'document_bundle', 'documents': {
        'delivery_art': {'title': 'Fictional art delivery', 'sections': [
            {'key': 'activities', 'label': 'Art activities', 'text': 'Fictional first original'}]},
        'delivery': {'title': 'Fictional delivery', 'sections': [
            {'key': 'art_activities', 'label': 'Delivery art activities', 'text': 'Fictional second original'}]}}}
    draft = materials().save_material(owner, program['id'], payload(content=content), None)
    path = f"/programs/{program['id']}/materials/{draft['id']}"
    token = csrf_token(login_client, path)
    page = login_client.get(path)
    # Submit names actually rendered by the editor, including duplicate controls
    # when reproducing the old collision; a dict would conceal this browser behavior.
    fields = MultiDict({key: value for key, value in payload().items() if key != 'content'})
    fields.add('expected_revision', '1')
    fields.add('csrf_token', token)
    edits = {'Fictional first original': 'First edited', 'Fictional second original': 'Second edited'}
    for name, original in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', page.text, re.S):
        if original in edits:
            fields.add(name, edits[original])
    response = login_client.post(path, data=fields)
    assert response.status_code == 302
    saved = materials().get_material(owner, program['id'], draft['id'])['content']['documents']
    assert [saved['delivery_art']['sections'][0]['text'], saved['delivery']['sections'][0]['text']] == ['First edited', 'Second edited']


def test_browser_improvement_requires_explicit_content_selection(login_client, owner, program, csrf_token):
    _, version = released(owner, program)
    derivative = materials().derive_material(owner, program['id'], version['id'])
    path = f"/programs/{program['id']}/materials/{derivative['id']}"
    token = csrf_token(login_client, path)
    response = login_client.post(path + '/improve', data={'note': 'Fictional note only', 'expected_revision': 1, 'csrf_token': token})
    assert response.status_code == 302
    assert materials().list_improvements(owner, program['id'])[0]['proposed_content'] is None
    response = login_client.post(path + '/improve', data={'note': 'Fictional intentional sharing', 'expected_revision': 1, 'share_content': 'yes', 'csrf_token': token})
    assert response.status_code == 302
    assert materials().list_improvements(owner, program['id'])[1]['proposed_content']['text'] == 'Fictional first version'
    response = login_client.post(path + '/improve', data={'note': 'Fictional stale preserved note', 'expected_revision': 0, 'share_content': 'yes', 'csrf_token': token})
    assert response.status_code == 409
    assert 'Fictional stale preserved note' in response.text
    assert len(materials().list_improvements(owner, program['id'])) == 2

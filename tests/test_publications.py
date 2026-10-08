"""Public library versions do not depend on design approval or disclose private evidence."""
import io
import json
import sqlite3
from zipfile import ZipFile

import pytest

from onpf.errors import DomainError


def test_unapproved_publication_is_frozen_downloadable_and_private(owner, program, submitted, app):
    from onpf.db import get_db
    from onpf.programs.service import get_program, save_document
    from onpf.publications.service import get_publication, publish
    from onpf.publications.files import package_bytes
    from onpf.permissions.service import save_permission
    from test_permissions import permission_payload

    save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 PRIVATE-PERMISSION')
    current = get_program(owner, program['id'])
    current = save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Fictional public purpose'}}, current['revision'])
    published = publish(owner, program['id'], current['revision'], reviewed=True)
    assert published['approval_status'] == 'unapproved'
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('UPDATE publications SET approval_status=? WHERE id=?', ('approved', published['id']))
    assert get_db().execute('SELECT COUNT(*) FROM releases').fetchone()[0] == 0
    source = get_publication(program['id'], 1)
    assert source['documents']['overview']['sections'][0]['text'] == 'Fictional public purpose'
    archive = package_bytes(source)
    with ZipFile(io.BytesIO(archive)) as bundle:
        names = set(bundle.namelist())
        assert all(f'documents/{key}.odt' in names and f'documents/{key}.html' in names and f'documents/{key}.md' in names for key in source['documents'])
        assert 'LICENSE' in names and 'manifest.json' in names
        assert json.loads(bundle.read('source/program.json'))['approval_status'] == 'unapproved'
        text = b'\n'.join(bundle.read(name) for name in names if not name.endswith('.odt'))
        assert b'PRIVATE-PERMISSION' not in text
        assert b'Fictional first perspective' not in text
    current = get_program(owner, program['id'])
    save_document(owner, program['id'], 'overview', {'sections': {'purpose': 'Changed later'}}, current['revision'])
    assert get_publication(program['id'], 1)['documents']['overview']['sections'][0]['text'] == 'Fictional public purpose'


def test_public_routes_show_only_explicit_versions_and_download_without_login(owner, program, app):
    from onpf.publications.service import publish
    browser = app.test_client()
    root = f"/projects/{program['id']}/versions/1"
    assert browser.get('/projects').status_code == 200
    assert program['title'].encode() not in browser.get('/projects').data
    assert browser.get(root).status_code == 404
    publish(owner, program['id'], program['revision'], reviewed=True)
    listing = browser.get('/projects')
    assert program['title'].encode() in listing.data
    detail = browser.get(root)
    assert b'unapproved design' in detail.data
    assert browser.get(root + '/documents/overview.odt').status_code == 200
    assert browser.get(root + '/documents/overview.html').status_code == 200
    assert browser.get(root + '/package.zip').mimetype == 'application/zip'


def test_publication_requires_owner_review_and_current_revision(owner, facilitator, program):
    from onpf.publications.service import publish
    with pytest.raises(DomainError) as error:
        publish(owner, program['id'], program['revision'], reviewed=False)
    assert error.value.code == 'publication_review_required'
    with pytest.raises(DomainError) as error:
        publish(facilitator, program['id'], program['revision'], reviewed=True)
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        publish(owner, program['id'], program['revision'] + 1, reviewed=True)
    assert error.value.code == 'stale_revision'


def test_approved_publication_keeps_real_status(owner, program):
    from onpf.releases.service import approve_candidate, prepare_candidate
    from onpf.publications.service import publish
    candidate = prepare_candidate(owner, program['id'], program['revision'])
    assert approve_candidate(owner, candidate['id'])['state'] == 'released'
    published = publish(owner, program['id'], program['revision'], reviewed=True)
    assert published['approval_status'] == 'approved'


def test_release_preflight_flags_draft_stage_wording_for_human_review(owner, program, login_client):
    from onpf.programs.service import get_program, save_document
    from onpf.releases.service import lifecycle_warnings, prepare_candidate
    current = get_program(owner, program['id'])
    current = save_document(owner, program['id'], 'overview', {'sections': {'status': 'These program documents are working drafts.'}}, current['revision'])
    warnings = lifecycle_warnings(current)
    assert any('Program overview / Design and operating status' in text for text in warnings)
    page = login_client.get(f"/programs/{program['id']}/releases")
    assert b'Review draft-stage wording' in page.data
    candidate = prepare_candidate(owner, program['id'], current['revision'])
    assert candidate['lifecycle_warnings'] == warnings


def test_public_page_labels_frozen_and_current_permission_evidence(owner, program, app):
    from onpf.permissions.service import revoke_permission, save_permission
    from onpf.publications.service import get_publication, publish
    from onpf.programs.service import get_program
    from test_permissions import permission_payload

    publish(owner, program['id'], program['revision'], reviewed=True)
    source = get_publication(program['id'], 1)
    assert source['permission_evidence_status'] == 'missing'
    record = save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 fictional')
    page = app.test_client().get(f"/projects/{program['id']}/versions/1").text
    assert 'filed permission evidence at publication <strong>missing</strong>' in page
    assert 'current filed evidence <strong>current</strong>' in page
    revoke_permission(owner, record['id'], '2026-10-03', 'Fictional steward withdrew access')
    assert get_publication(program['id'], 1)['permission_evidence_status'] == 'missing'
    assert 'current filed evidence <strong>revoked</strong>' in app.test_client().get(f"/projects/{program['id']}/versions/1").text


def test_publication_preview_displays_literal_reusable_material_content(owner, program, login_client):
    from onpf.materials.service import adopt_material, release_material, save_material
    from onpf.programs.service import get_program
    from test_materials import payload

    draft = save_material(owner, program['id'], payload('FICTIONAL REVIEW THIS MATERIAL CONTENT'), None)
    version = release_material(owner, draft['id'], draft['revision'])
    adopt_material(owner, program['id'], version['id'], get_program(owner, program['id'])['revision'])
    page = login_client.get(f"/programs/{program['id']}/publish")
    assert page.status_code == 200
    assert 'FICTIONAL REVIEW THIS MATERIAL CONTENT' in page.text
    assert 'Copyright Fictional authors' in page.text

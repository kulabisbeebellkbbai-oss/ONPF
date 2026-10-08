"""Operating permission evidence remains private and separate from design approval."""
import pytest
import sqlite3
from datetime import date, timedelta

from onpf.errors import DomainError


def permission_payload(**overrides):
    today = date.today()
    return {
        'issuer': 'Fictional site steward',
        'effective_on': (today - timedelta(days=1)).isoformat(),
        'expires_on': (today + timedelta(days=365)).isoformat(),
        'scope': 'Fictional garden site use',
        'conditions': 'Fictional daytime access only',
        'filed_on': today.isoformat(),
        'evidence_name': 'permission.pdf',
        'copies': [
            {'key': 'site_owner', 'label': 'Bob', 'state': 'delivered', 'date': today.isoformat()},
            {'key': 'coordinating_group', 'label': 'Coordinating group', 'state': 'delivered', 'date': today.isoformat()},
            {'key': 'official_record', 'label': 'Official program record', 'state': 'delivered', 'date': today.isoformat()},
            {'key': 'site_posting', 'label': 'Inside shed', 'state': 'posted', 'date': today.isoformat()},
        ],
        **overrides,
    }


def test_permission_record_and_copy_distribution_are_separate_from_decision(owner, program):
    from onpf.permissions.service import save_permission, permission_readiness, list_permissions
    from onpf.programs.service import get_program
    from onpf.db import get_db
    record = save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 fictional')
    assert record['version'] == 1
    assert record['copies'][0]['label'] == 'Bob'
    assert record['copies'][3]['state'] == 'posted'
    assert permission_readiness(program['id'])['status'] == 'current'
    assert get_program(owner, program['id'])['operating_status'] == 'pending'
    assert get_db().execute('SELECT COUNT(*) FROM decisions').fetchone()[0] == 0
    assert len(list_permissions(owner, program['id'])) == 1
    with pytest.raises(sqlite3.IntegrityError):
        get_db().execute('UPDATE permission_records SET scope=? WHERE id=?', ('Changed', record['id']))


def test_unfiled_revoked_and_replaced_permission_remain_traceable(owner, program):
    from onpf.permissions.service import save_permission, revoke_permission, permission_readiness, list_permissions
    unfiled = save_permission(owner, program['id'], permission_payload(filed_on='', evidence_name='', copies=[]), None)
    assert permission_readiness(program['id'])['status'] == 'unfiled'
    filed = save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 fictional')
    assert filed['replaces_id'] == unfiled['id']
    assert permission_readiness(program['id'])['status'] == 'current'
    revoke_permission(owner, filed['id'], '2026-10-03', 'Fictional steward withdrew access')
    assert permission_readiness(program['id'])['status'] == 'revoked'
    assert [row['version'] for row in list_permissions(owner, program['id'])] == [1, 2]


def test_permission_evidence_is_private_even_to_project_viewer(owner, other_owner, program, app):
    from onpf.auth.service import set_membership
    from onpf.permissions.service import get_permission, save_permission
    record = save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 PRIVATE-FICTIONAL-EVIDENCE')
    set_membership(owner, program['id'], other_owner.user_id, 'viewer')
    with pytest.raises(DomainError) as error:
        get_permission(other_owner, record['id'])
    assert error.value.status == 403
    from onpf.auth.service import create_session
    with app.app_context():
        token = create_session(other_owner)
    browser = app.test_client()
    browser.set_cookie('onpf_session', token)
    assert browser.get(f"/programs/{program['id']}/permissions/{record['id']}/download").status_code == 403
    assert b'PRIVATE-FICTIONAL-EVIDENCE' not in browser.get(f"/programs/{program['id']}").data


def test_garden_candidate_requires_explicit_permission_re_review(owner):
    from onpf.programs.service import create_program, get_program
    from onpf.releases.service import prepare_candidate, approve_candidate
    garden = create_program(owner, {'title': 'Fictional garden', 'module_keys': ['gardening']})
    with pytest.raises(DomainError) as error:
        prepare_candidate(owner, garden['id'], garden['revision'])
    assert error.value.code == 'permission_review_required'
    candidate = prepare_candidate(owner, garden['id'], get_program(owner, garden['id'])['revision'], permission_reviewed=True)
    with pytest.raises(DomainError) as error:
        approve_candidate(owner, candidate['id'])
    assert error.value.code == 'permission_review_required'
    released = approve_candidate(owner, candidate['id'], permission_reviewed=True)
    assert released['state'] == 'released'
    assert get_program(owner, garden['id'])['operating_status'] == 'pending'


def test_permission_form_saves_and_downloads_private_attachment(login_client, owner, program, csrf_token):
    root = f"/programs/{program['id']}/permissions"
    page = login_client.get(root)
    assert page.status_code == 200
    token = csrf_token(login_client, root)
    values = permission_payload()
    posted = login_client.post(root, data={
        'csrf_token': token, 'issuer': values['issuer'], 'effective_on': values['effective_on'],
        'expires_on': values['expires_on'], 'scope': values['scope'], 'conditions': values['conditions'],
        'filed_on': values['filed_on'], 'evidence_name': 'permission.pdf',
        'evidence': ( __import__('io').BytesIO(b'%PDF-1.4 fictional'), 'permission.pdf'),
        **{f"copy_{row['key']}_label": row['label'] for row in values['copies']},
        **{f"copy_{row['key']}_state": row['state'] for row in values['copies']},
        **{f"copy_{row['key']}_date": row['date'] for row in values['copies']},
    }, content_type='multipart/form-data')
    assert posted.status_code == 302
    from onpf.permissions.service import list_permissions
    record = list_permissions(owner, program['id'])[0]
    downloaded = login_client.get(f"{root}/{record['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.data == b'%PDF-1.4 fictional'
    assert 'attachment' in downloaded.headers['Content-Disposition']


def test_private_backup_restores_permission_attachment(app, owner, program, tmp_path):
    from pathlib import Path
    from onpf.archives.service import backup_private, restore_private
    from onpf.app import create_app
    from onpf.permissions.service import get_permission, save_permission

    record = save_permission(owner, program['id'], permission_payload(), b'%PDF-1.4 FICTIONAL-PRIVATE')
    archive = tmp_path / 'private.json'
    backup_private(Path(app.config['DATABASE']), archive)
    restored_path = tmp_path / 'restored.sqlite3'
    restore_private(archive, restored_path)
    restored = create_app({'TESTING': True, 'DATABASE': str(restored_path), 'INSTANCE_PATH': str(tmp_path / 'restored-instance')})
    with restored.app_context():
        assert get_permission(owner, record['id'])['evidence'] == b'%PDF-1.4 FICTIONAL-PRIVATE'

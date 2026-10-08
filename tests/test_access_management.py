"""Project access and installation administration are separate authorities."""
import pytest

from onpf.errors import DomainError


def test_first_account_is_admin_and_can_disable_creation(owner, facilitator, program):
    from onpf.auth.service import account_permissions, set_project_creation
    from onpf.programs.service import create_program, get_program
    assert account_permissions(owner)['is_admin']
    assert account_permissions(facilitator)['can_create_projects']
    set_project_creation(owner, facilitator.user_id, False)
    assert not account_permissions(facilitator)['can_create_projects']
    with pytest.raises(DomainError) as error:
        create_program(facilitator, {'title': 'Denied new program'})
    assert error.value.status == 403
    assert get_program(facilitator, program['id'])['id'] == program['id']


def test_viewer_reads_assigned_workspace_and_documents_but_cannot_write(owner, other_owner, program, app):
    from onpf.auth.service import set_membership
    from onpf.programs.service import get_program, save_document
    set_membership(owner, program['id'], other_owner.user_id, 'viewer')
    view = get_program(other_owner, program['id'])
    assert view['documents']['overview']['sections']['purpose'] == ''
    assert view['memberships'] == []
    with pytest.raises(DomainError) as error:
        save_document(other_owner, program['id'], 'overview', {'sections': {'purpose': 'Blocked'}}, view['revision'])
    assert error.value.status == 403
    from onpf.auth.service import create_session
    with app.app_context():
        token = create_session(other_owner)
    browser = app.test_client()
    browser.set_cookie('onpf_session', token)
    workspace = browser.get(f"/programs/{program['id']}")
    document = browser.get(f"/programs/{program['id']}/documents/overview")
    assert workspace.status_code == document.status_code == 200
    assert 'Save draft' not in document.text
    assert 'Refine contributions' not in workspace.text
    assert browser.post(f"/programs/{program['id']}/documents/overview", data={'expected_revision': view['revision']}).status_code in (400, 403)


def test_project_owner_is_not_installation_admin(owner, facilitator, other_owner, program):
    from onpf.auth.service import account_permissions, set_membership, set_project_creation
    from onpf.programs.service import create_program, get_program
    independent = create_program(other_owner, {'title': 'Independent program'})
    assert not account_permissions(facilitator)['is_admin']
    with pytest.raises(DomainError) as error:
        set_project_creation(facilitator, other_owner.user_id, False)
    assert error.value.status == 403
    with pytest.raises(DomainError) as error:
        set_membership(facilitator, independent['id'], facilitator.user_id, 'owner')
    assert error.value.status == 403
    set_membership(owner, independent['id'], facilitator.user_id, 'viewer')
    assert get_program(facilitator, independent['id'])['id'] == independent['id']


def test_creation_paths_and_console_enforce_saved_access(login_client, owner, facilitator, program, csrf_token, app):
    from onpf.auth.service import create_session, set_project_creation
    set_project_creation(owner, facilitator.user_id, False)
    with app.app_context():
        token = create_session(facilitator)
    browser = app.test_client()
    browser.set_cookie('onpf_session', token)
    assert browser.get('/programs/new').status_code == 403
    assert browser.get('/exports/import').status_code == 403
    assert browser.post('/programs/setup-working-programs').status_code in (400, 403)
    assert browser.get('/manage').status_code == 403
    assert login_client.get('/manage').location == '/admin/'
    page = login_client.get('/admin/')
    assert page.status_code == 200
    assert 'Users and access' in page.text
    assert program['title'] in page.text
    token = csrf_token(login_client, '/admin/')
    changed = login_client.post('/admin/', data={
        'csrf_token': token, 'action': 'membership', 'program_id': program['id'],
        'user_id': facilitator.user_id, 'role': 'viewer',
    })
    assert changed.status_code == 302
    from onpf.auth.service import management_state
    state = management_state(owner)
    member = next(row for row in state['memberships'] if row['program_id'] == program['id'] and row['user_id'] == facilitator.user_id)
    assert member['role'] == 'viewer'
    assert not next(row for row in state['users'] if row['id'] == facilitator.user_id)['can_create_projects']


def test_viewer_direct_posts_are_denied_with_valid_csrf(owner, other_owner, program, app, csrf_token):
    from onpf.auth.service import create_session, set_membership
    set_membership(owner, program['id'], other_owner.user_id, 'viewer')
    with app.app_context():
        session = create_session(other_owner)
    browser = app.test_client()
    browser.set_cookie('onpf_session', session)
    path = f"/programs/{program['id']}/documents/overview"
    token = csrf_token(browser, path)
    assert browser.post(path, data={'csrf_token': token, 'expected_revision': program['revision'], 'section_purpose': 'Forbidden'}).status_code == 403
    assert browser.post(f"/programs/{program['id']}/settings", data={'csrf_token': token, 'expected_revision': program['revision'], 'title': 'Forbidden'}).status_code == 403


def test_creation_denial_covers_setup_and_import_direct_posts(owner, facilitator, program, app, csrf_token):
    from onpf.auth.service import create_session, set_project_creation
    set_project_creation(owner, facilitator.user_id, False)
    with app.app_context():
        session = create_session(facilitator)
    browser = app.test_client()
    browser.set_cookie('onpf_session', session)
    token = csrf_token(browser, '/workspace')
    assert browser.post('/programs/setup-working-programs', data={'csrf_token': token}).status_code == 403
    assert browser.post('/exports/import', data={'csrf_token': token, 'title': 'Forbidden'}).status_code == 403
    from onpf.programs.service import list_programs
    with app.app_context():
        assert [item['id'] for item in list_programs(facilitator)] == [program['id']]


def test_local_cli_can_recover_installation_admin(tmp_path):
    import sqlite3
    import subprocess
    import sys
    instance = tmp_path / 'instance'
    base = [sys.executable, '-m', 'onpf.cli', '--instance', str(instance)]
    for name in ('first', 'recovery'):
        result = subprocess.run(base + ['create-user', '--username', name, '--password-stdin'], input='fictional-secure-password\n', text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    result = subprocess.run(base + ['grant-admin', '--username', 'recovery'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(instance / 'onpf.sqlite3') as connection:
        assert connection.execute("SELECT is_admin FROM users WHERE username='recovery'").fetchone()[0] == 1


def test_upgrade_keeps_existing_membership_and_bootstraps_admin(tmp_path):
    import sqlite3
    from pathlib import Path
    from onpf.db import migrate
    path = tmp_path / 'old.sqlite3'
    migrations = Path(__file__).parents[1] / 'src' / 'onpf' / 'migrations'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT NOT NULL)')
        for source in sorted(migrations.glob('*.sql')):
            if source.name >= '010_refinement_evidence.sql':
                continue
            connection.executescript(source.read_text(encoding='utf-8-sig'))
            connection.execute('INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)', (source.name, '2026-01-01'))
        connection.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES ('old-user','old','hash','2026-01-01')")
        connection.execute("INSERT INTO programs(id,title,created_at,updated_at) VALUES ('old-program','Old project','2026-01-01','2026-01-01')")
        connection.execute("INSERT INTO memberships(program_id,user_id,role) VALUES ('old-program','old-user','owner')")
    migrate(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT role FROM memberships WHERE program_id='old-program'").fetchone()[0] == 'owner'
        assert connection.execute("SELECT is_admin,can_create_projects FROM users WHERE id='old-user'").fetchone() == (1, 1)

"""Production storage refusal and real HTTP proxy/security acceptance checks."""
import http.client
import io
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest

HOST = 'onpf.example.org'
ORIGIN = 'https://' + HOST


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from onpf.app import create_app
    root = tmp_path / 'data'
    instance = root / 'instance'
    create_app({'INSTANCE_PATH': str(instance)})
    marker = root / '.onpf-volume-id'
    marker.write_text('vol-0123456789abcdef0\n', encoding='utf-8')
    marker.chmod(0o600)
    # Windows cannot provision a Linux mount; only this OS boundary is replaced.
    monkeypatch.setattr(os.path, 'ismount', lambda path: Path(path) == root)
    return {'ONPF_PUBLIC_HOST': HOST, 'ONPF_DATA_ROOT': str(root),
            'ONPF_INSTANCE_PATH': str(instance), 'ONPF_DATABASE': str(instance / 'onpf.sqlite3'),
            'ONPF_VOLUME_ID': 'vol-0123456789abcdef0'}


def production_app(environment):
    from onpf.production import create_production_app, load_config
    return create_production_app(load_config(environment))


@pytest.mark.parametrize('host', ['', '*', '*.example.org', 'https://onpf.example.org',
                                  'onpf.example.org:443', 'localhost', '127.0.0.1',
                                  'a..org', '-onpf.example.org', 'onpf_.org', 'onpf.example.org/path',
                                  'onpf.example.org\n', 'a.org;touch bad', 'a' * 64 + '.org'])
def test_invalid_public_host_is_rejected_without_writes(environment, host):
    from onpf.production import load_config
    environment['ONPF_PUBLIC_HOST'] = host
    instance = Path(environment['ONPF_INSTANCE_PATH'])
    before = {p.name: p.read_bytes() for p in instance.iterdir()}
    with pytest.raises(ValueError, match='ONPF_PUBLIC_HOST'):
        load_config(environment)
    assert {p.name: p.read_bytes() for p in instance.iterdir()} == before


def test_valid_configuration_and_local_defaults(environment, tmp_path):
    from onpf.production import load_config
    from onpf.app import create_app
    config = load_config(environment)
    assert config['DATABASE'] == environment['ONPF_DATABASE']
    assert config['SESSION_COOKIE_SECURE'] is True
    assert config['PREFERRED_URL_SCHEME'] == 'https'
    assert create_app({'INSTANCE_PATH': str(tmp_path / 'local')}).config['SESSION_COOKIE_SECURE'] is False


def test_contact_recipient_override_survives_production_revalidation(environment):
    from onpf.production import create_production_app, load_config
    environment.update(ONPF_CONTACT_TO='admin@example.org', ONPF_SMTP_HOST='smtp.example.org',
                       ONPF_SMTP_FROM='sender@example.org')
    app = create_production_app(load_config(environment))
    assert app.config['CONTACT_TO'] == 'admin@example.org'
    assert app.config['CONTACT_SMTP_HOST'] == 'smtp.example.org'
    page = app.test_client().get('/contact', base_url=ORIGIN)
    assert page.status_code == 200
    assert 'admin@example.org' not in page.text


def test_ai_configuration_survives_revalidation_without_loading_key_into_app(environment, tmp_path):
    key_file = tmp_path / 'provider-key'
    key_file.write_text('fictional-secret-key')
    key_file.chmod(0o600)
    environment.update(ONPF_AI_DRAFTING_ENABLED='true', ONPF_AI_GATEWAY_URL='https://gateway.example/v1', ONPF_AI_MODEL='fictional-model',
                       ONPF_AI_GATEWAY_KEY_FILE=str(key_file), ONPF_AI_MAX_OUTPUT_TOKENS='512')
    app = production_app(environment)
    assert app.config['AI_MODEL'] == 'fictional-model'
    assert 'AI_API_KEY' not in app.config
    assert app.config['AI_GATEWAY_KEY_FILE'] == str(key_file)
    assert app.config['AI_MAX_OUTPUT_TOKENS'] == 512


def test_production_rejects_raw_ai_key_without_storage_changes(environment):
    from onpf.production import load_config
    environment.update(ONPF_AI_BASE_URL='https://provider.example/v1', ONPF_AI_MODEL='fictional',
                       ONPF_AI_API_KEY='private-value')
    with pytest.raises(ValueError) as error:
        load_config(environment)
    assert 'protected credential file' in str(error.value)
    assert 'private-value' not in str(error.value)


@pytest.mark.parametrize('change', ['unmounted', 'missing_marker', 'wrong_marker',
                                    'missing_database', 'empty_database', 'other_database',
                                    'relative_root', 'relative_instance', 'relative_database',
                                    'escaped_instance', 'escaped_database', 'unknown_schema'])
def test_invalid_storage_fails_before_factory_writes(environment, change, monkeypatch, tmp_path):
    from onpf.production import load_config
    root = Path(environment['ONPF_DATA_ROOT'])
    instance = Path(environment['ONPF_INSTANCE_PATH'])
    database = Path(environment['ONPF_DATABASE'])
    if change == 'unmounted':
        monkeypatch.setattr(os.path, 'ismount', lambda _: False)
    elif change == 'missing_marker':
        (root / '.onpf-volume-id').unlink()
    elif change == 'wrong_marker':
        (root / '.onpf-volume-id').write_text('vol-fffffffffffffffff')
    elif change == 'missing_database':
        database.unlink()
    elif change == 'empty_database':
        database.write_bytes(b'')
    elif change == 'other_database':
        database.unlink()
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE unrelated (value TEXT)')
    elif change == 'unknown_schema':
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO schema_migrations VALUES ('999_future.sql','today')")
    else:
        variable = 'ONPF_' + change.split('_')[1].upper()
        if variable == 'ONPF_INSTANCE':
            variable = 'ONPF_INSTANCE_PATH'
        if variable == 'ONPF_ROOT':
            variable = 'ONPF_DATA_ROOT'
        environment[variable] = 'relative' if change.startswith('relative') else str(tmp_path / 'outside')
    (instance / '.secret').unlink()
    (instance / '.instance-id').unlink()
    with pytest.raises(ValueError):
        load_config(environment)
    assert not (instance / '.secret').exists()
    assert not (instance / '.instance-id').exists()
    assert not (tmp_path / 'outside').exists()
    if change == 'missing_database':
        assert not database.exists()


def test_factory_revalidates_storage_and_cannot_disable_security(environment):
    from onpf.production import create_production_app, load_config
    config = load_config(environment)
    config['SESSION_COOKIE_SECURE'] = False
    config['DEBUG'] = True
    config['EXPORT_DIRECTORY'] = str(Path(config['DATA_ROOT']).parent / 'outside-exports')
    app = create_production_app(config)
    assert app.config['SESSION_COOKIE_SECURE'] is True
    assert app.debug is False
    assert app.config['EXPORT_DIRECTORY'] == str(Path(app.instance_path) / 'exports')
    Path(config['DATABASE']).unlink()
    with pytest.raises(ValueError):
        create_production_app(config)
    assert not Path(config['DATABASE']).exists()


def test_real_mount_boundary_rejects_ordinary_directory(environment, monkeypatch):
    from onpf.production import load_config
    monkeypatch.undo()
    assert not os.path.ismount(environment['ONPF_DATA_ROOT'])
    with pytest.raises(ValueError, match='mounted data volume'):
        load_config(environment)


def test_existing_core_schema_can_receive_packaged_migrations(environment):
    from onpf.production import load_config
    from onpf import db
    database = Path(environment['ONPF_DATABASE'])
    database.unlink()
    with sqlite3.connect(database) as connection:
        connection.executescript((Path(db.__file__).parent / 'migrations' / '001_core.sql').read_text())
        connection.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        connection.execute("INSERT INTO schema_migrations VALUES ('001_core.sql','today')")
    load_config(environment)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0] == 1
    production_app(environment)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT 1 FROM schema_migrations WHERE version='008_archives.sql'").fetchone()


def test_environment_path_defaults_follow_selected_data_root(environment):
    from onpf.production import load_config
    environment.pop('ONPF_INSTANCE_PATH')
    environment.pop('ONPF_DATABASE')
    config = load_config(environment)
    assert config['INSTANCE_PATH'] == str(Path(environment['ONPF_DATA_ROOT']) / 'instance')
    assert config['DATABASE'] == str(Path(environment['ONPF_DATA_ROOT']) / 'instance' / 'onpf.sqlite3')


@pytest.mark.parametrize('target', ['instance', 'database', 'marker', 'secret', 'exports'])
def test_symlink_escapes_are_rejected(environment, tmp_path, target):
    from onpf.production import load_config
    root = Path(environment['ONPF_DATA_ROOT'])
    instance = Path(environment['ONPF_INSTANCE_PATH'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    paths = {'instance': root / 'linked-instance', 'database': instance / 'linked.sqlite3',
             'marker': root / '.onpf-volume-id', 'secret': instance / '.secret',
             'exports': instance / 'exports'}
    link = paths[target]
    if link.exists():
        link.unlink()
    destination = outside if target in {'instance', 'exports'} else outside / 'private'
    if destination != outside:
        destination.write_text('vol-0123456789abcdef0')
    try:
        link.symlink_to(destination, target_is_directory=destination.is_dir())
    except OSError:
        pytest.skip('OS does not grant symlink creation')
    if target == 'instance':
        environment['ONPF_INSTANCE_PATH'] = str(link)
    elif target == 'database':
        environment['ONPF_DATABASE'] = str(link)
    with pytest.raises(ValueError):
        load_config(environment)


def test_production_preserves_existing_identity_and_accounts(environment):
    from onpf.auth.service import create_user, authenticate
    first = production_app(environment)
    with first.app_context():
        user = create_user('owner', 'fictional-owner-password')
    second = production_app(environment)
    assert first.secret_key == second.secret_key
    assert first.config['INSTANCE_ID'] == second.config['INSTANCE_ID']
    with second.app_context():
        assert authenticate('owner', 'fictional-owner-password').user_id == user


def test_headers_host_and_https_guard(environment):
    client = production_app(environment).test_client()
    for path in ['/login', '/health', '/missing']:
        response = client.get(path, base_url=ORIGIN)
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['Referrer-Policy'] == 'same-origin'
    for origin in ['https://evil.example.org', 'https://child.' + HOST,
                   'https://' + HOST + ':8443', 'http://' + HOST]:
        response = client.post('/login', base_url=origin, data={'password': 'fictional-secret'})
        assert response.status_code == 400
        assert response.headers['Referrer-Policy'] == 'same-origin'
        assert 'fictional-secret' not in response.text


def test_flask_exception_logs_exclude_bearer_path_and_submitted_text(environment, caplog):
    app = production_app(environment)

    @app.get('/reviews/fictional-bearer-token')
    def fail():
        raise RuntimeError('fictional-submitted-private-text')

    response = app.test_client().get('/reviews/fictional-bearer-token', base_url=ORIGIN)
    assert response.status_code == 500
    assert caplog.records
    assert 'fictional-bearer-token' not in caplog.text
    assert 'fictional-submitted-private-text' not in caplog.text


def test_waitress_iteration_error_logs_exclude_bearer_path_and_submitted_text(environment, caplog):
    from flask import Response
    app = production_app(environment)

    @app.get('/reviews/fictional-stream-token')
    def streamed_failure():
        def generate():
            raise RuntimeError('fictional-stream-private-text')
            yield b''
        return Response(generate(), mimetype='text/plain')

    with live_server(app) as port:
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('GET', '/reviews/fictional-stream-token',
                               headers={'Host': HOST, 'X-Forwarded-Proto': 'https'})
            response = connection.getresponse()
            assert response.status == 500
            assert b'fictional-stream-private-text' not in response.read()
        finally:
            connection.close()
    assert caplog.records
    assert 'fictional-stream-token' not in caplog.text
    assert 'fictional-stream-private-text' not in caplog.text


def token(response):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


class ReferrerMetaParser(HTMLParser):
    policy = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name', '').lower() == 'referrer':
            self.policy = attrs.get('content')


def policy_referrer_headers(response, source_url, target_url):
    """Model the two policies relevant to this regression, as a browser would."""
    document = ReferrerMetaParser()
    document.feed(response.text)
    policy = document.policy or response.headers['Referrer-Policy']
    assert policy in {'no-referrer', 'same-origin'}
    source, target = urlsplit(source_url), urlsplit(target_url)
    if policy == 'same-origin' and (source.scheme, source.netloc) == (target.scheme, target.netloc):
        return {'Referer': source_url.split('#', 1)[0]}
    return {}


def test_response_policy_allows_secure_login_and_logout_without_weakening_csrf(environment):
    from onpf.auth.service import create_user
    app = production_app(environment)
    with app.app_context():
        create_user('owner', 'fictional-owner-password')
    client = app.test_client()
    page = client.get('/login', base_url=ORIGIN)
    # Derive headers from the served policy: injecting Referer unconditionally
    # hid the production bug where no-referrer prevented every HTTPS form POST.
    response = client.post('/login', base_url=ORIGIN,
                           headers=policy_referrer_headers(page, ORIGIN + '/login', ORIGIN + '/login'),
                           data={'username': 'owner', 'password': 'fictional-owner-password',
                                 'csrf_token': token(page)})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/workspace')
    page = client.get('/workspace', base_url=ORIGIN)
    assert page.status_code == 200
    assert policy_referrer_headers(page, ORIGIN + '/workspace', 'https://external.example.org/') == {}

    # Invalid attempts must neither bypass CSRF nor revoke the signed-in session.
    for headers, data in [
        ({'Referer': 'https://evil.example.org/'}, {'csrf_token': token(page)}),
        ({}, {'csrf_token': token(page)}),
        (policy_referrer_headers(page, ORIGIN + '/workspace', ORIGIN + '/logout'), {}),
    ]:
        assert client.post('/logout', base_url=ORIGIN, headers=headers, data=data).status_code == 400
        assert client.get('/workspace', base_url=ORIGIN).status_code == 200
    response = client.post('/logout', base_url=ORIGIN,
                           headers=policy_referrer_headers(page, ORIGIN + '/workspace', ORIGIN + '/logout'),
                           data={'csrf_token': token(page)})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/login')
    assert client.get('/workspace', base_url=ORIGIN).status_code == 302


def test_secure_auth_invitation_csrf_cookies_and_external_link(environment):
    from onpf.auth.models import Principal
    from onpf.auth.service import create_user
    from onpf.programs.service import create_program
    from onpf.inquiries.service import issue_batch
    app = production_app(environment)
    with app.app_context():
        actor = Principal(create_user('owner', 'fictional-owner-password'), None, None)
        program = create_program(actor, {'title': 'Fictional production program'})
        batch = issue_batch(actor, program['id'], {'title': 'Fictional batch', 'question_ids': ['core-1-1']})
    client = app.test_client()
    page = client.get('/login', base_url=ORIGIN)
    csrf_cookie = page.headers['Set-Cookie']
    assert all(flag in csrf_cookie for flag in ['Secure', 'HttpOnly', 'SameSite=Lax'])
    response = client.post('/login', base_url=ORIGIN,
                           headers=policy_referrer_headers(page, ORIGIN + '/login', ORIGIN + '/login'),
                           data={'username': 'owner', 'password': 'fictional-owner-password', 'csrf_token': token(page)})
    assert response.status_code == 302
    auth_cookie = next(c for c in response.headers.getlist('Set-Cookie') if c.startswith('onpf_session='))
    assert all(flag in auth_cookie for flag in ['Secure', 'HttpOnly', 'SameSite=Lax'])
    detail = client.get('/batches/' + batch['id'], base_url=ORIGIN)
    batch_path = '/batches/' + batch['id']
    response = client.post(batch_path + '/invitations', base_url=ORIGIN,
                           headers=policy_referrer_headers(detail, ORIGIN + batch_path, ORIGIN + batch_path + '/invitations'),
                           data={'csrf_token': token(detail)})
    assert response.status_code == 200
    link = re.search(r'https://onpf.example.org/invitations/answer#([^"<]+)', response.text)
    assert link, response.text
    guest = app.test_client()
    page = guest.get('/invitations/answer', base_url=ORIGIN)
    response = guest.post('/invitations/accept', base_url=ORIGIN,
                          headers=policy_referrer_headers(page, ORIGIN + '/invitations/answer', ORIGIN + '/invitations/accept'),
                          data={'token': link.group(1), 'csrf_token': token(page)})
    assert response.status_code == 302
    cookie = next(c for c in response.headers.getlist('Set-Cookie') if c.startswith('onpf_invitation='))
    assert all(flag in cookie for flag in ['Secure', 'HttpOnly', 'SameSite=Lax'])
    # Both invited and signed-in contributors use the same answer template;
    # a meta no-referrer override used to break their browser form submissions.
    for contributor, form_path, submit_path, text in [
        (guest, '/invitations/answer', '/invitations/contributions', 'Fictional guest response'),
        (client, batch_path + '/contributions', batch_path + '/contributions', 'Fictional owner response'),
    ]:
        page = contributor.get(form_path, base_url=ORIGIN)
        assert page.status_code == 200
        fields = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', page.text))
        response = contributor.post(submit_path, base_url=ORIGIN,
                                    headers=policy_referrer_headers(page, ORIGIN + form_path, ORIGIN + submit_path),
                                    data={'csrf_token': token(page), 'submission_key': fields['submission_key'],
                                          'row_id': '0', 'question_id_0': batch['questions'][0]['id'],
                                          'text_0': text})
        assert response.status_code == 302
        receipt_path = response.headers['Location']
        receipt = contributor.get(receipt_path, base_url=ORIGIN)
        assert receipt.status_code == 200
        assert '1 responses saved.' in receipt.text
        assert policy_referrer_headers(receipt, ORIGIN + receipt_path, ORIGIN + form_path) == {'Referer': ORIGIN + receipt_path}
        assert policy_referrer_headers(receipt, ORIGIN + receipt_path, 'https://external.example.org/') == {}
    from onpf.contributions.service import list_responses
    with app.app_context():
        assert {response['text'] for response in list_responses(actor, program['id'])} == {
            'Fictional guest response', 'Fictional owner response'}
    page = client.get('/workspace', base_url=ORIGIN)
    assert client.post('/logout', base_url=ORIGIN, headers={'Referer': 'https://evil.example.org/'},
                       data={'csrf_token': token(page)}).status_code == 400


@contextmanager
def live_server(app):
    from onpf.production import WAITRESS_OPTIONS, _private_server_logs
    from waitress import create_server
    options = dict(WAITRESS_OPTIONS, port=0, asyncore_loop_timeout=0.05)
    with _private_server_logs():
        server = create_server(app, **options)
        stop = Event()
        failures = []

        def serve():
            try:
                while not stop.is_set():
                    server.asyncore.loop(timeout=0.05, map=server._map, count=1)
            except BaseException as error:
                failures.append(error)

        thread = Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield int(server.effective_port)
        finally:
            stop.set()
            thread.join(timeout=3)
            assert not thread.is_alive()
            server.task_dispatcher.shutdown()
            server.close()
            if failures:
                raise failures[0]


def test_live_server_stops_loop_and_workers_before_closing_sockets(monkeypatch):
    from types import SimpleNamespace
    import waitress
    entered, release = Event(), Event()
    order = []
    active = []

    class StopEvent(Event):
        def set(self):
            order.append('stop')
            super().set()
            release.set()

    def loop(**options):
        active.append(True)
        entered.set()
        assert release.wait(2)
        active.clear()
        order.append('loop_exit')

    def close():
        was_active = bool(active)
        release.set()  # Make even the broken harness safe to exit after failure.
        order.append('close')
        assert not was_active, 'Sockets closed during an active serving iteration'

    fake = SimpleNamespace(effective_port=12345, _map={},
        asyncore=SimpleNamespace(loop=loop), run=lambda: loop(), close=close,
        task_dispatcher=SimpleNamespace(shutdown=lambda: order.append('workers_stop')))
    monkeypatch.setattr(waitress, 'create_server', lambda *a, **k: fake)
    monkeypatch.setattr(sys.modules[__name__], 'Event', StopEvent)
    with live_server(lambda *a: None) as port:
        assert port == 12345
        assert entered.wait(2)
    assert order == ['stop', 'loop_exit', 'workers_stop', 'close']


def test_live_server_propagates_genuine_loop_error_after_cleanup(monkeypatch):
    from types import SimpleNamespace
    import waitress
    failed = Event()
    order = []
    sentinel = RuntimeError('fictional serving failure')

    def loop(**options):
        failed.set()
        raise sentinel

    fake = SimpleNamespace(effective_port=12345, _map={},
        asyncore=SimpleNamespace(loop=loop), run=lambda: loop(),
        close=lambda: order.append('close'),
        task_dispatcher=SimpleNamespace(shutdown=lambda: order.append('workers_stop')))
    monkeypatch.setattr(waitress, 'create_server', lambda *a, **k: fake)
    with pytest.raises(RuntimeError) as caught:
        with live_server(lambda *a: None):
            assert failed.wait(2)
    assert caught.value is sentinel
    assert order == ['workers_stop', 'close']


def http_get(port, headers, source='127.0.0.1'):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3, source_address=(source, 0))
    try:
        connection.request('GET', '/health', headers=headers)
        response = connection.getresponse()
        body = response.read()
        return response.status, body
    finally:
        connection.close()


def test_waitress_trusts_only_loopback_peer_and_selected_headers(environment):
    with live_server(production_app(environment)) as port:
        valid = {'Host': HOST, 'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '192.0.2.9'}
        assert http_get(port, valid)[0] == 200
        assert http_get(port, dict(valid, **{'Forwarded': 'host=evil.example.org;proto=http',
                                            'X-Forwarded-Host': 'evil.example.org', 'X-Forwarded-Port': '8443'}))[0] == 200
        assert http_get(port, {'Host': HOST})[0] == 400
        assert http_get(port, dict(valid, **{'Host': 'evil.example.org'}))[0] == 400
        assert http_get(port, dict(valid, **{'X-Forwarded-Proto': 'http'}))[0] == 400
        assert http_get(port, dict(valid, **{'X-Forwarded-Proto': 'https,http'}))[0] == 400
        # A distinct loopback source is a real untrusted TCP peer, not an environ mock.
        assert http_get(port, valid, source='127.0.0.2')[0] == 400


def test_waitress_caps_import_body_before_reading_and_flask_keeps_route_limits(environment):
    with live_server(production_app(environment)) as port:
        headers = {'Host': HOST, 'X-Forwarded-Proto': 'https',
                   'Content-Length': str(26 * 1024 * 1024 + 1)}
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request('POST', '/exports/import', headers=headers)
            response = connection.getresponse()
            assert response.status == 413
            response.read()
        finally:
            connection.close()
    # Import gets the existing 26 MiB form envelope; other forms keep 2 MiB.
    client = production_app(environment).test_client()
    oversized_file = b'x' * (3 * 1024 * 1024)
    assert client.post('/login', base_url=ORIGIN,
                       data={'package': (io.BytesIO(oversized_file), 'fictional.zip')}).status_code == 413
    assert client.post('/exports/import', base_url=ORIGIN,
                       data={'package': (io.BytesIO(oversized_file), 'fictional.zip')}).status_code == 400


def test_entrypoint_reports_nonsecret_error_without_traceback(tmp_path):
    environment = dict(os.environ, ONPF_PUBLIC_HOST='invalid\nfictional-secret',
                       ONPF_DATA_ROOT=str(tmp_path / 'missing'), ONPF_VOLUME_ID='fictional-secret')
    result = subprocess.run([sys.executable, '-m', 'onpf.production'], env=environment,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'ONPF_PUBLIC_HOST' in result.stderr
    assert 'fictional-secret' not in result.stderr
    assert 'Traceback' not in result.stderr
    assert not (tmp_path / 'missing').exists()

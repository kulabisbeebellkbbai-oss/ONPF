"""The drafting transport's wire, configuration, bounds and privacy contract."""
import importlib
import json
import os
import socket
import time
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from threading import Event

import pytest


@contextmanager
def gateway_server(body=None, status=200, headers=None, delay=0, drip=False):
    requests = []
    payload = body if body is not None else json.dumps({
        'choices': [{'message': {'role': 'assistant', 'content': '{"draft":"A suggestion"}'},
                     'finish_reason': 'stop'}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            requests.append((self.path, dict(self.headers), self.rfile.read(int(self.headers['Content-Length']))))
            time.sleep(delay)
            try:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if drip:
                    for byte in payload:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.2)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def settings(tmp_path):
    key = tmp_path / 'gateway-key'
    key.write_text('fictional-private-key\n', encoding='utf-8')
    key.chmod(0o600)
    return {'AI_DRAFTING_ENABLED': True, 'AI_GATEWAY_KEY_FILE': str(key)}


def modules():
    return (importlib.import_module('onpf.drafting.config'),
            importlib.import_module('onpf.drafting.gateway'))


def test_gateway_wire_request_and_bounded_response(settings):
    config, gateway = modules()
    messages = [{'role': 'user', 'content': 'Fictional evidence'}]
    with gateway_server() as (url, requests):
        result = gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url,
                                                          'AI_MODEL': 'configured-alias',
                                                          'AI_MAX_OUTPUT_TOKENS': 512}), messages)
    assert result == '{"draft":"A suggestion"}'
    assert len(requests) == 1
    path, headers, raw = requests[0]
    assert path == '/v1/chat/completions'
    assert headers['Authorization'] == 'Bearer fictional-private-key'
    assert headers['Content-Type'] == 'application/json'
    assert json.loads(raw) == {'model': 'configured-alias', 'messages': messages,
                               'max_tokens': 512, 'stream': False,
                               'response_format': {'type': 'json_object'}}


def test_defaults_and_matching_environment_names(settings):
    config, _ = modules()
    mapped = config.drafting_environment({'ONPF_AI_DRAFTING_ENABLED': 'true',
        'ONPF_AI_GATEWAY_KEY_FILE': settings['AI_GATEWAY_KEY_FILE'],
        'ONPF_AI_TIMEOUT_SECONDS': '30', 'ONPF_AI_MAX_OUTPUT_TOKENS': '8192',
        'ONPF_AI_MAX_EVIDENCE_BYTES': '1024', 'UNRELATED': 'ignored'})
    normalized = config.validate_settings(mapped)
    assert normalized == {**settings, 'AI_GATEWAY_URL': 'http://127.0.0.1:4000/v1',
        'AI_MODEL': 'onpf-drafting', 'AI_TIMEOUT_SECONDS': 30,
        'AI_MAX_OUTPUT_TOKENS': 8192, 'AI_MAX_EVIDENCE_BYTES': 1024}
    defaults = config.validate_settings(settings)
    assert defaults['AI_TIMEOUT_SECONDS'] == 45
    assert defaults['AI_MAX_OUTPUT_TOKENS'] == 4096
    assert defaults['AI_MAX_EVIDENCE_BYTES'] == 96000
    assert config.validate_settings({})['AI_DRAFTING_ENABLED'] is False


def test_incomplete_chunked_response_is_sanitized_and_not_retried(settings):
    config, gateway = modules()
    # A valid first chunk followed by premature EOF inside the next chunk.
    # HTTP framing fails before content validation; this covers IncompleteRead.
    with gateway_server(body=b'4\r\nLEAK\r\nA\r\nsecret',
                        headers={'Transfer-Encoding': 'chunked', 'Connection': 'close'}) as (url, requests):
        with pytest.raises(gateway.GatewayError) as caught:
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}),
                             [{'role': 'user', 'content': 'Fictional evidence'}])
    assert str(caught.value) == 'The drafting gateway is unavailable.'
    assert caught.value.__suppress_context__ is True
    assert len(requests) == 1


@pytest.mark.parametrize('value', [False, 'false', '0', 'off'])
def test_disabled_performs_no_credential_or_network_reads(value, monkeypatch):
    config, gateway = modules()
    def forbidden(*_, **__):
        pytest.fail('Disabled drafting performed external I/O')
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    normalized = config.validate_settings({'AI_DRAFTING_ENABLED': value,
        'AI_GATEWAY_URL': 'invalid private configuration', 'AI_GATEWAY_KEY_FILE': '/missing'})
    with pytest.raises(gateway.GatewayError, match='disabled'):
        gateway.complete(normalized, [{'role': 'user', 'content': 'Private evidence'}])


@pytest.mark.parametrize('url', ['https://gateway.example.org/v1',
    'http://127.0.0.1:4000/v1', 'http://[::1]:4000/v1', 'http://127.0.0.2/v1'])
def test_https_or_literal_loopback_urls_are_accepted(settings, url):
    config, _ = modules()
    assert config.validate_settings({**settings, 'AI_GATEWAY_URL': url})['AI_GATEWAY_URL'] == url


@pytest.mark.parametrize('url', ['http://localhost:4000/v1', 'http://gateway.example.org/v1',
    'http://192.168.1.2/v1', 'ftp://127.0.0.1/v1', 'https://user:private@gateway.example/v1',
    'https://gateway.example/v1?key=private', 'https://gateway.example/v1#private',
    'https://gateway.example/v1\r\nAuthorization: private', ' https://gateway.example/v1',
    'https://gateway.example\\@127.0.0.1/v1', 'http://127.1/v1',
    'http://2130706433/v1', 'http://127.0.0.1:0/v1', 'https://gateway.example:99999/v1',
    'https://gateway.example/v1/../private', 'https://gateway.example/v1/%2e%2e/private',
    'https://gateway.example/v1%0d%0aheader', 'https://gateway.example/v1?'])
def test_unsafe_or_injected_urls_are_rejected_without_echo(settings, url):
    config, _ = modules()
    with pytest.raises(ValueError) as error:
        config.validate_settings({**settings, 'AI_GATEWAY_URL': url})
    assert url not in str(error.value)
    assert 'private' not in str(error.value)


@pytest.mark.parametrize('name,value', [('AI_TIMEOUT_SECONDS', 0), ('AI_TIMEOUT_SECONDS', 61),
    ('AI_TIMEOUT_SECONDS', '1.5'), ('AI_TIMEOUT_SECONDS', True),
    ('AI_MAX_OUTPUT_TOKENS', 255), ('AI_MAX_OUTPUT_TOKENS', 8193),
    ('AI_MAX_EVIDENCE_BYTES', 1023), ('AI_MAX_EVIDENCE_BYTES', 96001),
    ('AI_DRAFTING_ENABLED', 'maybe'), ('AI_MODEL', ''), ('AI_MODEL', 'alias\nprivate'),
    ('AI_GATEWAY_KEY_FILE', ''), ('AI_GATEWAY_KEY_FILE', 'relative-private-key')])
def test_invalid_settings_are_rejected(settings, name, value):
    config, _ = modules()
    with pytest.raises(ValueError):
        config.validate_settings({**settings, name: value})


def test_enabled_validation_does_not_read_the_key(settings, monkeypatch):
    config, _ = modules()
    def forbidden(*_, **__):
        pytest.fail('Configuration validation read a credential')
    monkeypatch.setattr(os, 'open', forbidden)
    assert config.validate_settings(settings)['AI_DRAFTING_ENABLED'] is True


@pytest.mark.parametrize('change', ['missing', 'empty', 'oversized', 'injected', 'nonutf8', 'directory'])
def test_invalid_key_fails_before_any_http_request(settings, change):
    config, gateway = modules()
    path = Path(settings['AI_GATEWAY_KEY_FILE'])
    if change == 'missing': path.unlink()
    elif change == 'directory':
        path.unlink()
        path.mkdir()
    else:
        path.write_bytes({'empty': b'', 'oversized': b'k' * 4097,
                          'injected': b'key\r\nHeader: private', 'nonutf8': b'\xff'}[change])
    with gateway_server() as (url, requests):
        with pytest.raises(gateway.GatewayError):
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}), [])
    assert requests == []


@pytest.mark.skipif(os.name == 'nt', reason='Unix credential permissions are enforced on Unix')
def test_credential_requires_private_unix_permissions(settings):
    config, gateway = modules()
    Path(settings['AI_GATEWAY_KEY_FILE']).chmod(0o640)
    with pytest.raises(gateway.GatewayError, match='credential'):
        gateway.complete(config.validate_settings(settings), [])


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 401, 429, 500])
def test_redirects_and_http_errors_are_not_retried_or_echoed(settings, status):
    config, gateway = modules()
    with gateway_server(body=b'private upstream response', status=status,
                        headers={'Location': 'http://127.0.0.1:1/private'}) as (url, requests):
        with pytest.raises(gateway.GatewayError) as error:
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}), [])
    assert len(requests) == 1
    formatted = ''.join(traceback.format_exception(error.value))
    assert 'private upstream response' not in formatted
    assert 'fictional-private-key' not in formatted
    assert '127.0.0.1:1/private' not in formatted


@pytest.mark.parametrize('body', [b'x' * 131073, b'not json private', b'\xff', b'{}',
    b'{"choices": []}', b'{"choices": [{"message": {"content": null}}]}',
    b'{"choices": [{"message": {"content": ["private"]}}]}',
    b'{"choices": [{"message": {"content": ""}}]}',
    b'{"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}'],
    ids=['oversized', 'invalid-json', 'invalid-utf8', 'no-choices', 'empty-choices',
         'null-content', 'list-content', 'empty-content', 'truncated'])
def test_oversized_malformed_or_truncated_responses_fail_privately(settings, body):
    config, gateway = modules()
    with gateway_server(body=body) as (url, _):
        with pytest.raises(gateway.GatewayError) as error:
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}), [])
    formatted = ''.join(traceback.format_exception(error.value))
    assert 'not json private' not in formatted
    assert '["private"]' not in formatted


def test_exact_response_byte_cap_is_accepted(settings):
    config, gateway = modules()
    body = b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
    body += b' ' * (131072 - len(body))
    with gateway_server(body=body) as (url, _):
        assert gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}), []) == '{}'


def test_incomplete_content_length_cannot_return_a_suggestion(settings):
    config, gateway = modules()
    body = b'{"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]}'
    with gateway_server(body=body, headers={'Content-Length': str(len(body) + 10)}) as (url, _):
        with pytest.raises(gateway.GatewayError, match='invalid'):
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url}), [])


def test_request_body_cap_rejects_before_network_or_key_reads(settings, monkeypatch):
    config, gateway = modules()
    def forbidden(*_, **__): pytest.fail('Oversized request performed external I/O')
    monkeypatch.setattr(os, 'open', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    with pytest.raises(gateway.GatewayError, match='request'):
        gateway.complete(config.validate_settings(settings), [{'role': 'user', 'content': 'é' * 66000}])


@pytest.mark.parametrize('drip', [False, True])
def test_timeout_is_bounded_including_a_slow_drip_response(settings, drip):
    config, gateway = modules()
    with gateway_server(delay=0 if drip else 2, drip=drip) as (url, _):
        started = time.monotonic()
        with pytest.raises(gateway.GatewayError, match='unavailable'):
            gateway.complete(config.validate_settings({**settings, 'AI_GATEWAY_URL': url,
                                                       'AI_TIMEOUT_SECONDS': 1}), [])
        assert time.monotonic() - started < 1.8


def test_connection_exception_is_sanitized(settings, monkeypatch, caplog):
    config, gateway = modules()
    def fail(*_, **__):
        raise OSError('fictional-private-key and private prompt leaked by upstream')
    monkeypatch.setattr(socket, 'socket', fail)
    messages = [{'role': 'user', 'content': 'private prompt'}]
    with pytest.raises(gateway.GatewayError) as error:
        gateway.complete(config.validate_settings(settings), messages)
    formatted = ''.join(traceback.format_exception(error.value))
    assert 'fictional-private-key' not in formatted
    assert 'private prompt' not in formatted
    assert not caplog.records


def test_stalled_dns_has_a_deadline_and_cannot_send_a_late_request(settings, monkeypatch):
    config, gateway = modules()
    release = Event()
    finished = Event()
    connections = []
    def resolve(*_, **__):
        try:
            release.wait(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        finally:
            finished.set()
    def forbidden(*_, **__):
        connections.append(True)
        raise AssertionError('Timed-out DNS initiated a connection')
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(socket, 'socket', forbidden)
    started = time.monotonic()
    try:
        with pytest.raises(gateway.GatewayError, match='unavailable'):
            gateway.complete(config.validate_settings({**settings,
                'AI_GATEWAY_URL': 'https://gateway.example.org/v1', 'AI_TIMEOUT_SECONDS': 1}), [])
        assert time.monotonic() - started < 1.8
    finally:
        release.set()
        assert finished.wait(1)
    assert connections == []


def test_repeated_dns_timeouts_have_bounded_resolver_concurrency(settings, monkeypatch):
    config, gateway = modules()
    release = Event()
    calls = []
    def resolve(*_, **__):
        calls.append(True)
        release.wait(10)
        return []
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    try:
        for _ in range(6):
            with pytest.raises(gateway.GatewayError, match='unavailable'):
                gateway.complete(config.validate_settings({**settings,
                    'AI_GATEWAY_URL': 'https://gateway.example.org/v1', 'AI_TIMEOUT_SECONDS': 1}), [])
        assert 1 <= len(calls) <= 4
    finally:
        release.set()


def test_local_factory_loads_and_validates_environment_before_state_writes(tmp_path, settings, monkeypatch):
    from onpf.app import create_app
    monkeypatch.setenv('ONPF_AI_DRAFTING_ENABLED', 'true')
    monkeypatch.setenv('ONPF_AI_GATEWAY_KEY_FILE', settings['AI_GATEWAY_KEY_FILE'])
    monkeypatch.setenv('ONPF_AI_MAX_OUTPUT_TOKENS', '512')
    instance = tmp_path / 'local'
    app = create_app({'INSTANCE_PATH': str(instance)})
    assert app.config['AI_DRAFTING_ENABLED'] is True
    assert app.config['AI_MAX_OUTPUT_TOKENS'] == 512
    monkeypatch.setenv('ONPF_AI_GATEWAY_URL', 'http://remote.example/v1')
    invalid = tmp_path / 'invalid'
    with pytest.raises(ValueError):
        create_app({'INSTANCE_PATH': str(invalid)})
    assert not invalid.exists()


def test_production_factory_preserves_ai_settings_and_revalidates(tmp_path, settings, monkeypatch):
    from onpf.app import create_app
    from onpf.production import load_config, create_production_app
    root = tmp_path / 'data'
    instance = root / 'instance'
    create_app({'INSTANCE_PATH': str(instance)})
    (root / '.onpf-volume-id').write_text('vol-0123456789abcdef0')
    (root / '.onpf-volume-id').chmod(0o600)
    monkeypatch.setattr(os.path, 'ismount', lambda path: Path(path) == root)
    environment = {'ONPF_PUBLIC_HOST': 'onpf.example.org', 'ONPF_DATA_ROOT': str(root),
                   'ONPF_VOLUME_ID': 'vol-0123456789abcdef0',
                   **{f'ONPF_{key}': value for key, value in settings.items()},
                   'ONPF_AI_MODEL': 'configured-alias', 'ONPF_AI_MAX_EVIDENCE_BYTES': '2048'}
    config = load_config(environment)
    app = create_production_app(config)
    assert app.config['AI_DRAFTING_ENABLED'] is True
    assert app.config['AI_MODEL'] == 'configured-alias'
    assert app.config['AI_MAX_EVIDENCE_BYTES'] == 2048
    config['AI_GATEWAY_URL'] = 'http://remote.example/v1'
    with pytest.raises(ValueError):
        create_production_app(config)

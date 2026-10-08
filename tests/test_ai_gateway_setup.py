"""Private gateway deployment; host mutations are confined to fake Linux edges."""
import hashlib
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / 'deploy/aws'


def helper():
    spec = importlib.util.spec_from_file_location('setup_ai_gateway', AWS / 'setup_ai_gateway.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return json.loads((AWS / 'ai-gateway.example.json').read_text())


def test_validate_nonsecret_config_without_reading_credentials(monkeypatch):
    gateway = helper()
    selected = config()
    monkeypatch.setattr(gateway, 'read_secret', lambda *args: pytest.fail('credential read'))
    assert gateway.validate_config(selected) == selected


@pytest.mark.parametrize('field,value', [
    ('bind_host', '0.0.0.0'), ('port', 4001), ('port', True),
    ('gateway_version', 'latest'), ('gateway_version', '1.103.1'),
    ('model_id', 'openai/o3'), ('model_id', 'openai/x\nBAD=value'),
    ('runtime_root', '/opt/onpf'), ('config_root', '/etc/onpf'),
    ('master_key_file', '/tmp/key'), ('provider_key_file', '/etc/../tmp/key'),
    ('python', '/tmp/python'), ('lock_sha256', '0' * 64),
    ('OPENAI_API_KEY', 'fictional-secret'),
])
def test_rejects_unsafe_operator_configuration(field, value):
    selected = config()
    selected[field] = value
    with pytest.raises(ValueError):
        helper().validate_config(selected)


def test_rendered_contract_and_persistent_app_settings():
    gateway = helper()
    selected = config()
    yaml = gateway.gateway_yaml(selected)
    assert 'model_name: onpf-drafting' in yaml
    assert 'model: openai/gpt-4o-mini-2024-07-18' in yaml
    assert 'api_base: https://api.openai.com/v1' in yaml
    for text in ('telemetry: false', 'cache: false', 'drop_params: false',
                 'callbacks: []', 'no-log: true', 'disable_error_logs: true',
                 'disable_spend_logs: true', 'fallbacks: []'):
        assert text in yaml
    assert 'database_url' not in yaml
    environment = gateway.application_environment()
    from onpf.drafting.config import drafting_environment
    mapped = drafting_environment(dict(line.split('=', 1) for line in environment.splitlines()))
    assert mapped['AI_DRAFTING_ENABLED'] == 'true'
    assert 'ONPF_AI_GATEWAY_KEY_FILE=/etc/onpf/ai-gateway-key' in environment
    assert 'OPENAI' not in environment and 'LITELLM_MASTER_KEY' not in environment
    app_service = (AWS / 'templates/onpf.service').read_text()
    assert 'EnvironmentFile=-/etc/onpf/ai-drafting.env' in app_service
    service = (AWS / 'templates/onpf-ai-gateway.service').read_text()
    for text in ('User=onpf-ai-gateway', 'Group=onpf-ai-gateway', 'UMask=0077',
                 'StandardOutput=null', 'StandardError=null', 'LimitCORE=0',
                 'ProtectSystem=strict', 'ProtectHome=true', 'NoNewPrivileges=true',
                 'InaccessiblePaths=/var/lib/onpf /etc/onpf /opt/onpf'):
        assert text in service
    assert 'ReadWritePaths' not in service
    for name in ('nginx-http.conf', 'nginx-https.conf'):
        assert '4000' not in (AWS / 'templates' / name).read_text()
    assert '4000' not in (AWS / 'infrastructure.json').read_text()


def test_complete_target_lock_has_reviewed_wheels_and_linux_markers():
    metadata = json.loads((AWS / 'gateway-lock-metadata.json').read_text())
    data = (AWS / 'gateway-requirements-linux-py312.lock').read_bytes()
    assert metadata['lock_sha256'] == hashlib.sha256(data).hexdigest()
    assert metadata['target'] == 'x86_64-unknown-linux-gnu'
    assert metadata['python_version'] == '3.12.14'
    names = {p['name'] for p in metadata['packages']}
    assert {'litellm', 'uvloop', 'pyroscope-io', 'uvicorn', 'litellm-proxy-extras'} <= names
    assert 'colorama' not in names  # Windows-only dependency must not leak into target graph.
    for package in metadata['packages']:
        assert package['wheel'].endswith('.whl')
        assert package['sha256'] in data.decode()
    assert 'litellm' not in (ROOT / 'requirements.lock').read_text().lower()


def test_install_refuses_nonlinux_before_mutation(monkeypatch):
    gateway = helper()
    monkeypatch.setattr(gateway, 'linux_host', lambda: False)
    monkeypatch.setattr(gateway, 'command', lambda *args, **kwargs: pytest.fail('host command'))
    with pytest.raises(ValueError, match='unsupported_host'):
        gateway.install(config())


def test_install_existing_target_and_redirect_refused_before_commands(tmp_path, monkeypatch):
    gateway = helper()
    old = tmp_path / 'old'
    old.mkdir()
    with pytest.raises(ValueError, match='existing_target'):
        gateway.new_target(old)
    nested = old / '..' / 'new'
    with pytest.raises(ValueError, match='unsafe_path'):
        gateway.safe_path(nested)
    if os.name != 'nt':
        link = tmp_path / 'link'
        link.symlink_to(old, target_is_directory=True)
        with pytest.raises(ValueError, match='unsafe_path'):
            gateway.safe_path(link / 'new')


def test_credential_reader_bounded_private_and_generic(tmp_path):
    gateway = helper()
    path = tmp_path / 'secret'
    path.write_text('sk-fictional-secret-01234567890123456789\n')
    path.chmod(0o600)
    assert gateway.read_secret(path, os.getuid() if hasattr(os, 'getuid') else None).startswith('sk-')
    for value in ('', 'sk-echo\nINJECT=private', 'sk-' + 'x' * 4096):
        path.write_text(value)
        with pytest.raises(ValueError, match='invalid_credential') as error:
            gateway.read_secret(path, None)
        assert value not in str(error.value) or value == ''


def test_launcher_fixed_uvicorn_and_sanitized_environment():
    spec = importlib.util.spec_from_file_location('launch_ai_gateway', AWS / 'launch_ai_gateway.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    env = launcher.child_environment({'OPENAI_API_KEY': 'sk-fictional-provider-0123456789',
        'LITELLM_MASTER_KEY': 'sk-fictional-gateway-0123456789', 'DATABASE_URL': 'danger',
        'LITELLM_PRINT_STANDARD_LOGGING_PAYLOAD': 'false', 'HTTP_PROXY': 'danger'})
    assert 'DATABASE_URL' not in env and 'HTTP_PROXY' not in env
    assert 'LITELLM_PRINT_STANDARD_LOGGING_PAYLOAD' not in env
    worker = json.loads(env['WORKER_CONFIG'])
    assert worker['telemetry'] is False and worker['drop_params'] is False
    assert worker['request_timeout'] == 30
    args = launcher.child_command('/runtime/python')
    assert args[1:4] == ['-m', 'uvicorn', 'gateway_asgi:app']
    assert args[args.index('--host') + 1] == '127.0.0.1'
    assert args[args.index('--port') + 1] == '4000'
    assert '--no-access-log' in args
    assert '--workers' in args and args[args.index('--workers') + 1] == '1'
    # A reviewed future local route may stop requiring an OpenAI key without
    # changing the application's alias or inheriting arbitrary provider env.
    local = launcher.child_environment({'LITELLM_MASTER_KEY':'sk-fictional-gateway-0123456789'})
    assert 'OPENAI_API_KEY' not in local


def test_cli_error_never_echoes_configuration(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{"OPENAI_API_KEY": "SYNTHETIC-SECRET-SENTINEL"}')
    result = subprocess.run([sys.executable, str(AWS / 'setup_ai_gateway.py'),
                             'validate', '--config', str(path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'SYNTHETIC-SECRET-SENTINEL' not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


def test_install_only_provisions_separate_gateway_and_preserves_production(tmp_path, monkeypatch):
    gateway = helper()
    runtime = tmp_path / 'runtime'
    etc = tmp_path / 'gateway-config'
    app = tmp_path / 'app-config'
    app.mkdir()
    original = app / 'production.env'
    original.write_text('UNRELATED_SETTING=preserved\n')
    for name, value in {'RUNTIME': runtime, 'ETC': etc, 'UNIT': tmp_path / 'gateway.service',
                        'APP_ENV': app / 'ai-drafting.env', 'APP_KEY': app / 'ai-gateway-key'}.items():
        monkeypatch.setattr(gateway, name, value)
    monkeypatch.setattr(gateway, 'preflight', lambda c: None)
    monkeypatch.setattr(gateway, 'account', lambda: None)
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=777)))
    monkeypatch.setattr(gateway, 'read_secret', lambda path, uid: (
        'sk-fictional-gateway-01234567890123456789' if str(path).endswith('master-key')
        else 'sk-fictional-provider-01234567890123456789'))
    ownership = []
    monkeypatch.setattr(gateway.shutil, 'chown', lambda path, **kwargs: ownership.append((Path(path), kwargs)))
    commands = []
    def fake_linux_command(args):
        commands.append(args)
        if 'venv' in args and '-m' in args:
            (runtime / 'venv/bin').mkdir(parents=True)
            (runtime / 'venv/bin/python').write_text('fictional interpreter')
    monkeypatch.setattr(gateway, 'command', fake_linux_command)
    gateway.install(config())
    assert original.read_text() == 'UNRELATED_SETTING=preserved\n'
    # The installer normalizes text to LF on Linux; Windows checkouts may use CRLF.
    assert (runtime / 'launch_ai_gateway.py').read_text() == (AWS / 'launch_ai_gateway.py').read_text()
    assert (runtime / 'gateway_asgi.py').read_text() == (AWS / 'gateway_asgi.py').read_text()
    assert 'current' not in (tmp_path / 'gateway.service').read_text()
    assert 'OPENAI_API_KEY' not in (app / 'ai-drafting.env').read_text()
    assert 'sk-fictional-provider' not in (app / 'ai-gateway-key').read_text()
    assert any(path == app / 'ai-gateway-key' and owner == {'user':'onpf','group':'onpf'}
               for path, owner in ownership)
    assert any('--require-hashes' in cmd and '--only-binary=:all:' in cmd for cmd in commands)
    assert all('onpf.cli' not in cmd and 'stop' not in cmd and 'start' not in cmd for cmd in commands)
    assert all(cmd[:4] == ['/usr/sbin/runuser', '-u', 'onpf-ai-gateway', '--']
               for cmd in commands if str(runtime / 'venv/bin/python') in cmd)
    before = (app / 'ai-drafting.env').read_bytes()
    with pytest.raises(FileExistsError):
        gateway.install(config())
    assert (app / 'ai-drafting.env').read_bytes() == before


@pytest.mark.skipif(os.name == 'nt', reason='Unix ownership/permissions require Linux acceptance')
def test_secret_group_readability_and_symlinks_refused(tmp_path):
    gateway = helper()
    path = tmp_path / 'key'
    path.write_text('sk-fictional-gateway-01234567890123456789')
    path.chmod(0o640)
    with pytest.raises(ValueError, match='invalid_credential'):
        gateway.read_secret(path, os.getuid())
    path.chmod(0o400)
    with pytest.raises(ValueError, match='invalid_credential'):
        gateway.read_secret(path, os.getuid() + 1)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(ValueError, match='invalid_credential'):
        gateway.read_secret(link, os.getuid())


def surface(monkeypatch):
    calls = []
    async def upstream(scope, receive, send):
        calls.append((scope, receive, send))
    monkeypatch.setitem(sys.modules, 'litellm.proxy.proxy_server', SimpleNamespace(app=upstream))
    spec = importlib.util.spec_from_file_location('gateway_asgi', AWS / 'gateway_asgi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls


@pytest.mark.parametrize('method,path', [
    ('GET', '/'), ('GET', '/ui'), ('GET', '/ui/'), ('GET', '/ui/index.html'),
    ('GET', '/ui/assets/script.js'), ('GET', '/assets/script.js'),
    ('GET', '/_next/static/chunks/script.js'),
    ('GET', '/litellm-asset-prefix/_next/static/chunks/script.js'),
    ('GET', '/docs'), ('GET', '/openapi.json'), ('GET', '/v1/models'),
    ('GET', '/health'), ('GET', '/health/liveliness'), ('GET', '/health/readiness'),
    ('POST', '/key/generate'), ('GET', '/sso/debug/callback'),
    ('GET', '/v1/chat/completions'), ('POST', '/v1/chat/completions/'),
])
def test_gateway_http_surface_denies_ui_assets_and_unrelated_routes(monkeypatch, method, path):
    gateway, calls = surface(monkeypatch)
    sent = []
    async def receive():
        pytest.fail('blocked request body read')
    async def send(message):
        sent.append(message)
    asyncio.run(gateway.app({'type':'http', 'method':method, 'path':path,
                            'headers':[(b'authorization', b'Bearer fictional-master-key')]}, receive, send))
    assert calls == []
    assert sent == [{'type':'http.response.start','status':404,
                     'headers':[(b'content-type',b'application/json'), (b'content-length',b'21')]},
                    {'type':'http.response.body','body':b'{"error":"not_found"}'}]


@pytest.mark.parametrize('scope', [{'type':'lifespan'},
                                 {'type':'http','method':'POST','path':'/v1/chat/completions'}])
def test_gateway_surface_preserves_startup_and_authenticated_completion(monkeypatch, scope):
    gateway, calls = surface(monkeypatch)
    async def receive():
        return {'type':'lifespan.startup'}
    async def send(message):
        pass
    asyncio.run(gateway.app(scope, receive, send))
    assert calls == [(scope, receive, send)]


def test_gateway_surface_denies_websockets(monkeypatch):
    gateway, calls = surface(monkeypatch)
    sent = []
    async def receive():
        pytest.fail('websocket read')
    async def send(message):
        sent.append(message)
    asyncio.run(gateway.app({'type':'websocket','path':'/ui'},receive,send))
    assert calls == [] and sent == [{'type':'websocket.close','code':1008}]

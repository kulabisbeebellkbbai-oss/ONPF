"""Rendered proxy contracts plus actual Windows-compatible Waitress HTTP checks.

These template inspections are not execution or acceptance of Linux Nginx.
"""
import configparser
import http.client
import re
import threading

import pytest

from test_aws_tools import tool, config


def test_rendered_https_has_single_canonical_authority_and_private_logs(config):
    install = tool('install')
    rendered = install.render_template('nginx-https.conf', config)
    headers = dict(re.findall(r'proxy_set_header\s+(\S+)\s+([^;]+);', rendered))
    assert headers == {'Host': 'onpf.example.org', 'X-Forwarded-Proto': 'https',
                       'X-Forwarded-For': '$remote_addr', 'Forwarded': '""',
                       'X-Forwarded-Host': '""', 'X-Forwarded-Port': '""', 'X-Forwarded-By': '""'}
    assert re.findall(r'(?m)^\s*access_log\s+([^;]+);', rendered) == ['off']
    assert re.findall(r'(?m)^\s*error_log\s+([^;]+);', rendered) == ['/dev/null']
    assert 'client_max_body_size 26m;' in rendered
    assert 'ssl_reject_handshake on;' in rendered
    assert 'return 444;' in rendered
    assert '/var/lib/onpf' not in rendered
    assert 'proxy_request_buffering off;' in rendered
    assert 'proxy_buffering off;' in rendered
    assert 'return 308 https://onpf.example.org$request_uri;' in rendered


def test_http_bootstrap_exposes_only_acme_and_never_application(config):
    rendered = tool('install').render_template('nginx-http.conf', config)
    assert 'proxy_pass' not in rendered
    assert 'return 503;' in rendered
    assert 'return 444;' in rendered
    assert 'root /var/www/onpf-acme;' in rendered


def test_units_fail_on_mount_loss_and_bound_restart_and_timer(config):
    install = tool('install')
    for name in ('onpf.service', 'onpf-backup.service'):
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.read_string(install.render_template(name, config))
        assert unit['Unit']['BindsTo'] == 'var-lib-onpf.mount'
        assert unit['Service']['User'] == 'onpf'
        assert unit['Service']['UMask'] == '0077'
        assert unit['Service']['ProtectSystem'] == 'strict'
        assert unit['Service']['NoNewPrivileges'] == 'true'
    # systemd permits repeated EnvironmentFile entries and loads them in order.
    app = configparser.ConfigParser(interpolation=None, strict=False)
    app.read_string(install.render_template('onpf.service', config))
    assert int(app['Unit']['StartLimitBurst']) <= 5
    assert int(app['Service']['RestartSec']) >= 5
    timer = configparser.ConfigParser(interpolation=None)
    timer.read_string(install.render_template('onpf-backup.timer', config))
    assert timer['Timer']['Persistent'] == 'true'


def test_actual_waitress_secure_cookie_csrf_and_spoofed_headers(config, monkeypatch):
    from onpf.production import create_production_app, load_config, WAITRESS_OPTIONS
    from waitress.server import create_server
    install = tool('install')
    app = create_production_app(load_config(install.environment(config)))
    options = dict(WAITRESS_OPTIONS, port=0, asyncore_loop_timeout=0.05)
    server = create_server(app, **options)
    stop = threading.Event()
    def serve():
        while not stop.is_set():
            server.asyncore.loop(timeout=0.05, map=server._map, count=1)
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    def request(method, path, headers, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.effective_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read().decode()
        finally: connection.close()
    canonical = {'Host': 'onpf.example.org', 'X-Forwarded-Proto': 'https'}
    try:
        status, headers, body = request('GET', '/login', canonical)
        assert status == 200
        cookie = next(value for key, value in headers if key.lower() == 'set-cookie')
        assert all(flag in cookie for flag in ('Secure', 'HttpOnly', 'SameSite=Lax'))
        assert 'csrf_token' in body
        assert request('POST', '/login', canonical, 'username=fictional')[0] == 400
        assert request('GET', '/login', {**canonical, 'Host': 'hostile.example'})[0] == 400
        assert request('GET', '/login', {**canonical, 'X-Forwarded-Proto': 'http'})[0] == 400
        assert request('GET', '/login', {**canonical, 'X-Forwarded-Host': 'evil.example', 'Forwarded': 'host=evil.example;proto=http'})[0] == 200
        assert request('GET', '/var/lib/onpf/instance/onpf.sqlite3', canonical)[0] == 404
        assert request('POST', '/login', {**canonical, 'Content-Length': str(26 * 1024 * 1024 + 1)})[0] == 413
    finally:
        stop.set(); thread.join(timeout=3)
        server.close(); server.task_dispatcher.shutdown()
        assert not thread.is_alive()


@pytest.mark.parametrize('field,maximum', [('request_rate_per_second', 100),
                                          ('request_burst', 500),
                                          ('login_rate_per_minute', 60),
                                          ('login_burst', 30)])
def test_request_limit_configuration_rejects_invalid_values(config, field, maximum):
    install = tool('install')
    for value in (0, -1, maximum + 1, True, None, 1.5, '10', '1; return 200'):
        with pytest.raises(ValueError, match=field):
            install.render_template('nginx-https.conf', dict(config, **{field: value}))
    for value in (1, maximum):
        assert install.validate_config(dict(config, **{field: value}))[field] == value


@pytest.mark.parametrize('settings,expected', [({}, (10, 40, 6, 5)),
    ({'request_rate_per_second': 12, 'request_burst': 60,
      'login_rate_per_minute': 12, 'login_burst': 12}, (12, 60, 12, 12))])
def test_rendered_rate_limits_share_one_private_canonical_proxy(config, settings, expected):
    rendered = tool('install').render_template('nginx-https.conf', dict(config, **settings))
    general_rate, general_burst, login_rate, login_burst = expected
    assert f'limit_req_zone $binary_remote_addr zone=onpf_general:10m rate={general_rate}r/s;' in rendered
    assert f'limit_req_zone $onpf_login_key zone=onpf_login:10m rate={login_rate}r/m;' in rendered
    # Match the normalized URI (not args); only the real authentication POST is
    # expensive. Empty keys are excluded from the login zone by Nginx.
    assert re.search(r'map "\$request_method:\$uri" \$onpf_login_key \{\s*default "";\s*"POST:/login" \$binary_remote_addr;\s*\}', rendered)
    assert f'limit_req zone=onpf_general burst={general_burst} nodelay;' in rendered
    assert f'limit_req zone=onpf_login burst={login_burst} nodelay;' in rendered
    assert 'limit_req_status 429;' in rendered
    # All paths, including login and uploads, keep identical canonical headers,
    # body/buffering limits and privacy policy by using the SAME proxy location.
    assert rendered.count('proxy_pass ') == 1
    assert 'add_header Cache-Control "no-store" always;' in rendered
    # Browser form POSTs need a same-origin Referer for strict HTTPS CSRF.
    # Suppress the app header so an older release cannot add a conflicting policy.
    assert 'add_header Referrer-Policy "same-origin" always;' in rendered
    assert 'proxy_hide_header Referrer-Policy;' in rendered
    assert 'proxy_hide_header Cache-Control;' in rendered
    assert 'client_max_body_size 26m;' in rendered
    assert 'proxy_request_buffering off;' in rendered
    assert 'access_log off;' in rendered and 'error_log /dev/null;' in rendered
    assert '@@' not in rendered

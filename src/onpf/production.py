"""Fail-closed startup for an initialized ONPF installation behind Nginx.

Only Waitress interprets proxy headers. Bootstrap, not this module, mounts the
intended volume, writes its identity marker and initializes the installation.
"""
import ipaddress
import logging
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from flask import Flask
from waitress import serve
from werkzeug.wrappers import Response

from onpf.app import create_app
from onpf.config import contact_environment
from onpf.contact.service import valid_email
from onpf.drafting.config import DEFAULTS as DRAFTING_DEFAULTS, drafting_environment, validate_settings
from onpf.server_options import REQUEST_BODY_OPTIONS

WAITRESS_OPTIONS = {
    'host': '127.0.0.1', 'port': 8765, 'threads': 4,
    'trusted_proxy': '127.0.0.1', 'trusted_proxy_count': 1,
    'trusted_proxy_headers': {'x-forwarded-proto', 'x-forwarded-for'},
    'clear_untrusted_proxy_headers': True, 'log_untrusted_proxy_headers': False,
    'expose_tracebacks': False,
    **REQUEST_BODY_OPTIONS,
}


class _PrivateServerLogFilter(logging.Filter):
    def filter(self, record):
        # Waitress can include request paths even at INFO (disconnects), and
        # exception text may contain participant submissions. Retain severity
        # and timing only; do not let alternate handlers recover raw details.
        record.msg = 'ONPF production server event.'
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


@contextmanager
def _private_server_logs():
    """Sanitize Waitress records only while this production server is running."""
    sanitizer = _PrivateServerLogFilter()
    loggers = [logging.getLogger('waitress'), logging.getLogger('waitress.queue')]
    for logger in loggers:
        logger.addFilter(sanitizer)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(sanitizer)


def _public_host(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or '.' not in value:
        raise ValueError('ONPF_PUBLIC_HOST must be one public DNS hostname.')
    labels = value.split('.')
    if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in labels):
        raise ValueError('ONPF_PUBLIC_HOST must be one public DNS hostname.')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value.lower()
    raise ValueError('ONPF_PUBLIC_HOST must be one public DNS hostname.')


def _absolute(value: str, name: str) -> Path:
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(f'{name} must be an absolute path.')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'{name} must be an absolute path without traversal.')
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise ValueError(f'{name} cannot be resolved safely.') from None
    if resolved != path:
        raise ValueError(f'{name} must not use symlinks or redirected paths.')
    return path


def _contained(path: Path, root: Path, name: str) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError(f'{name} must stay below ONPF_DATA_ROOT.') from None
    if path == root:
        raise ValueError(f'{name} must stay below ONPF_DATA_ROOT.')
    _absolute(str(path), name)


def _initialized_database(database: Path) -> None:
    if not database.is_file() or database.stat().st_size == 0:
        raise ValueError('ONPF_DATABASE must be an existing initialized database.')
    try:
        # mode=ro prevents a missing/wrong database from being initialized.
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10)
        try:
            if connection.execute('PRAGMA quick_check(1)').fetchone()[0] != 'ok':
                raise ValueError('ONPF_DATABASE failed its integrity check.')
            applied = {row[0] for row in connection.execute('SELECT version FROM schema_migrations')}
            known = {p.name for p in (Path(__file__).parent / 'migrations').glob('*.sql')}
            if '001_core.sql' not in applied or not applied <= known:
                raise ValueError('ONPF_DATABASE has an uninitialized or unsupported schema.')
            # An initialization claim alone is insufficient: require the real
            # original ONPF tables. Later packaged migrations still run normally.
            for query in (
                'SELECT id,username,password_hash,created_at FROM users LIMIT 0',
                'SELECT id,title,revision,created_at,updated_at FROM programs LIMIT 0',
                'SELECT program_id,user_id,role FROM memberships LIMIT 0',
                'SELECT token_hash,user_id,created_at,expires_at FROM auth_sessions LIMIT 0',
                'SELECT username,failures,blocked_until FROM login_attempts LIMIT 0',
            ):
                connection.execute(query)
        finally:
            connection.close()
    except sqlite3.Error:
        raise ValueError('ONPF_DATABASE must have an initialized ONPF schema and be readable.') from None


def load_config(environment: Mapping[str, str]) -> dict:
    """Validate configuration and existing storage without initializing either."""
    host = _public_host(environment.get('ONPF_PUBLIC_HOST', ''))
    root = _absolute(environment.get('ONPF_DATA_ROOT', '/var/lib/onpf'), 'ONPF_DATA_ROOT')
    instance = _absolute(environment.get('ONPF_INSTANCE_PATH', str(root / 'instance')), 'ONPF_INSTANCE_PATH')
    database = _absolute(environment.get('ONPF_DATABASE', str(instance / 'onpf.sqlite3')), 'ONPF_DATABASE')
    volume_id = environment.get('ONPF_VOLUME_ID', '')
    if not isinstance(volume_id, str) or not re.fullmatch(r'vol-(?:[0-9a-f]{8}|[0-9a-f]{17})', volume_id):
        raise ValueError('ONPF_VOLUME_ID must identify the intended EBS data volume.')
    try:
        if not root.is_dir() or not os.path.ismount(root):
            raise ValueError('ONPF_DATA_ROOT must be the mounted data volume.')
        _contained(instance, root, 'ONPF_INSTANCE_PATH')
        _contained(database, root, 'ONPF_DATABASE')
        marker = root / '.onpf-volume-id'
        _contained(marker, root, 'Data volume identity marker')
        if not marker.is_file() or marker.stat().st_size > 80:
            raise ValueError('Data volume identity marker is missing or invalid.')
        if os.name != 'nt' and marker.stat().st_mode & 0o077:
            raise ValueError('Data volume identity marker must have private permissions.')
        if marker.read_text(encoding='utf-8').strip() != volume_id:
            raise ValueError('Data volume identity does not match ONPF_VOLUME_ID.')
        if not instance.is_dir():
            raise ValueError('ONPF_INSTANCE_PATH must be an existing initialized instance.')
        # These are all state paths written by the factory/application/SQLite.
        for path in (instance / '.secret', instance / '.instance-id', instance / 'exports',
                     instance / 'tmp', Path(str(database) + '-wal'),
                     Path(str(database) + '-shm'), Path(str(database) + '-journal')):
            _contained(path, root, 'Instance state')
        _initialized_database(database)
    except (OSError, UnicodeError):
        raise ValueError('Production storage is inaccessible or invalid.') from None
    contact = contact_environment(environment)
    if 'CONTACT_TO' in contact and not valid_email(contact['CONTACT_TO']):
        raise ValueError('ONPF_CONTACT_TO must be one email address.')
    if 'CONTACT_SMTP_FROM' in contact and contact['CONTACT_SMTP_FROM'] and not valid_email(contact['CONTACT_SMTP_FROM']):
        raise ValueError('ONPF_SMTP_FROM must be one email address.')
    if 'CONTACT_SMTP_PORT' in contact:
        try:
            port = int(contact['CONTACT_SMTP_PORT'])
        except (TypeError, ValueError):
            raise ValueError('ONPF_SMTP_PORT must be from 1 to 65535.') from None
        if not 1 <= port <= 65535:
            raise ValueError('ONPF_SMTP_PORT must be from 1 to 65535.')
        contact['CONTACT_SMTP_PORT'] = port
    return {'PUBLIC_HOST': host, 'DATA_ROOT': str(root), 'VOLUME_ID': volume_id,
            'INSTANCE_PATH': str(instance), 'DATABASE': str(database),
            'EXPORT_DIRECTORY': str(instance / 'exports'),
            'SESSION_COOKIE_SECURE': True, 'SESSION_COOKIE_HTTPONLY': True,
            'SESSION_COOKIE_SAMESITE': 'Lax', 'PREFERRED_URL_SCHEME': 'https',
            'SERVER_NAME': host, 'TRUSTED_HOSTS': [host], 'DEBUG': False, **contact,
            **validate_settings(drafting_environment(environment))}


def create_production_app(config: dict) -> Flask:
    """Revalidate just before the existing factory can write to persistent state."""
    # Reject obsolete provider inputs before supported-input filtering can hide
    # them. The application receives only the current gateway credential file.
    validate_settings(config)
    # Take only supported inputs, so caller overrides cannot disable security or
    # move exports elsewhere after load_config has validated the selected state.
    contact_variables = {f'ONPF_{key.removeprefix("CONTACT_")}': value for key, value in config.items()
                         if key.startswith('CONTACT_') and key != 'CONTACT_TO'}
    if 'CONTACT_TO' in config:
        contact_variables['ONPF_CONTACT_TO'] = config['CONTACT_TO']
    drafting_variables = {'ONPF_' + name: config[name] for name in DRAFTING_DEFAULTS if name in config}
    validated = load_config({'ONPF_PUBLIC_HOST': config.get('PUBLIC_HOST', ''),
                             'ONPF_DATA_ROOT': config.get('DATA_ROOT', ''),
                             'ONPF_VOLUME_ID': config.get('VOLUME_ID', ''),
                             'ONPF_INSTANCE_PATH': config.get('INSTANCE_PATH', ''),
                             'ONPF_DATABASE': config.get('DATABASE', ''), **contact_variables,
                             **drafting_variables})
    app = create_app(validated)
    host = validated['PUBLIC_HOST']

    def private_exception(_exc_info):
        # Override only this Flask instance; ordinary local application logging
        # keeps its behavior. Flask's default includes path and exception values.
        app.logger.error('ONPF production application request failed.')

    app.log_exception = private_exception

    @app.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        # HTTPS form CSRF validation requires a same-origin Referer. Keep that
        # header for this site while withholding it from external destinations.
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    application = app.wsgi_app

    def require_https_origin(environ, start_response):
        # This check precedes Flask's CSRF hook, so a downgraded scheme cannot
        # bypass HTTPS referer checking. Never derive authority from input headers.
        authority = environ.get('HTTP_HOST', '').lower()
        if authority not in {host, host + ':443'} or environ.get('wsgi.url_scheme') != 'https':
            return Response('A canonical HTTPS origin is required.', status=400,
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin'})(environ, start_response)
        return application(environ, start_response)

    app.wsgi_app = require_https_origin
    return app


def main() -> int:
    try:
        app = create_production_app(load_config(os.environ))
        # Waitress spills large bodies/output to tempfile; keep that private state
        # on the data mount as well, under the service's restrictive umask.
        scratch = Path(app.instance_path) / 'tmp'
        scratch.mkdir(mode=0o700, exist_ok=True)
        tempfile.tempdir = str(scratch)
        with _private_server_logs():
            serve(app, **WAITRESS_OPTIONS)
    except (ValueError, OSError, RuntimeError, sqlite3.Error):
        # Configuration values, database contents and request bodies never enter
        # the startup diagnostic. Keep validation messages readable and nonsecret.
        error = sys.exc_info()[1]
        detail = str(error) if isinstance(error, ValueError) else 'Initialized storage or loopback server is unavailable.'
        print('ONPF production startup failed: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

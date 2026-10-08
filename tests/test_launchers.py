"""Windows launcher readiness, safe reuse and unrelated-listener refusal."""
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryFile
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]


def launch(instance, port, database=None, instance_argument=None):
    # Background descendants on Windows can inherit unused pipe handles. File
    # capture waits for the launcher, without waiting for a running server's EOF.
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'scripts' / 'Start-ONPF.ps1'), '-InstancePath', instance_argument or str(instance), '-Port', str(port), '-NoBrowser']
        if database:
            command.extend(['-DatabasePath', str(database)])
        result = subprocess.run(command, cwd=instance.parent, stdout=stdout, stderr=stderr, timeout=35)
        stdout.seek(0)
        stderr.seek(0)
        result.stdout, result.stderr = stdout.read().decode(), stderr.read().decode()
        return result


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows launcher verification')
def test_windows_launcher_start_reuse_and_conflict(tmp_path):
    from onpf.app import create_app
    instance = tmp_path / 'fictional instance with spaces'
    create_app({'INSTANCE_PATH': str(instance)})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    try:
        first = launch(instance, port)
        assert first.returncode == 0, first.stdout + first.stderr
        assert 'Started ONPF' in first.stdout
        pid = (instance / '.server.pid').read_text().strip()
        second = launch(instance, port)
        assert second.returncode == 0 and 'Reusing ONPF' in second.stdout, second.stdout + second.stderr
        assert (instance / '.server.pid').read_text().strip() == pid
        alternate = tmp_path / 'different.sqlite3'
        create_app({'INSTANCE_PATH': str(instance), 'DATABASE': str(alternate)})
        mismatch = launch(instance, port, alternate)
        assert mismatch.returncode != 0 and 'Port conflict' in ' '.join(mismatch.stderr.split())
    finally:
        if (instance / '.server.pid').exists():
            subprocess.run(['taskkill', '/PID', (instance / '.server.pid').read_text().strip(), '/T', '/F'], capture_output=True)

    class Unrelated(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"application":"another-service"}')
        def log_message(self, *_args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Unrelated)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = launch(instance, server.server_port)
        assert result.returncode != 0 and 'Port conflict' in ' '.join(result.stderr.split())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows launcher verification')
def test_windows_launcher_requires_explicit_first_setup(tmp_path):
    result = launch(tmp_path / 'not initialized', 9876)
    assert result.returncode != 0 and 'first-time setup' in ' '.join(result.stderr.split())
    assert not (tmp_path / 'not initialized').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows launcher verification')
def test_windows_launcher_trailing_separator_preserves_explicit_database(tmp_path):
    from onpf.app import create_app
    import json
    import urllib.request
    instance = tmp_path / 'fictional spaced instance'
    database = tmp_path / 'fictional spaced database.sqlite3'
    app = create_app({'INSTANCE_PATH': str(instance), 'DATABASE': str(database)})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    try:
        result = launch(instance, port, database, instance_argument=str(instance) + '\\')
        assert result.returncode == 0, result.stdout + result.stderr
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
            assert json.load(response)['instance_id'] == app.config['INSTANCE_ID']
        assert not (instance / 'onpf.sqlite3').exists()
    finally:
        if (instance / '.server.pid').exists():
            subprocess.run(['taskkill', '/PID', (instance / '.server.pid').read_text().strip(), '/T', '/F'], capture_output=True)

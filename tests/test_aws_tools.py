"""Deployment boundaries: real files/archives/SQLite, external Linux/AWS edges faked."""
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    path = ROOT / 'deploy' / 'aws' / (name + '.py')
    assert path.is_file(), f'{name} deployment tool has not been implemented'
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for name, data in {'pyproject.toml': '[project]\nname="example"',
                       'requirements.lock': 'example==1', 'LICENSE': 'MIT',
                       'src/onpf/app.py': '# code\n', 'instance/private.json': 'PRIVATE',
                       '.env': 'SECRET', 'src/onpf/.secret': 'SECRET'}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode())
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'fixture']):
        subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
    return source


def test_bundle_reproducible_committed_only_and_no_private_files(source, tmp_path):
    build = tool('build_bundle')
    first, second = tmp_path / 'one.tar.gz', tmp_path / 'two.tar.gz'
    build.build_bundle(source, first)
    (source / 'src/onpf/app.py').write_text('unreviewed working copy')
    (source / 'src/onpf/untracked.py').write_text('untracked')
    build.build_bundle(source, second)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        assert set(archive.getnames()) == {'LICENSE', 'pyproject.toml', 'requirements.lock', 'src/onpf/app.py', 'release.json'}
        assert archive.extractfile('src/onpf/app.py').read() == b'# code\n'
    manifest = json.loads(Path(str(first) + '.sha256.json').read_text())
    assert manifest['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()


@pytest.mark.parametrize('name,kind', [('../escape', 'file'), ('/escape', 'file'), ('C:/escape', 'file'),
                                    ('src\\escape', 'file'), ('src/link', 'symlink'),
                                    ('src/link', 'hardlink'), ('src/fifo', 'fifo'), ('src/../escape', 'file')])
def test_extract_rejects_hostile_members_before_any_write(tmp_path, name, kind):
    build = tool('build_bundle')
    archive_path = tmp_path / 'bad.tar'
    with tarfile.open(archive_path, 'w') as archive:
        good = tarfile.TarInfo('LICENSE'); good.size = 4
        archive.addfile(good, io.BytesIO(b'good'))
        bad = tarfile.TarInfo(name)
        bad.type = {'file': tarfile.REGTYPE, 'symlink': tarfile.SYMTYPE,
                    'hardlink': tarfile.LNKTYPE, 'fifo': tarfile.FIFOTYPE}[kind]
        bad.linkname = '../outside'
        archive.addfile(bad, io.BytesIO())
    target = tmp_path / 'extracted'
    with pytest.raises(ValueError):
        build.extract_bundle(archive_path, hashlib.sha256(archive_path.read_bytes()).hexdigest(), target)
    assert not target.exists()
    assert not (tmp_path / 'escape').exists()


def test_extract_refuses_checksum_mismatch_without_writes(source, tmp_path):
    build = tool('build_bundle')
    bundle = tmp_path / 'bundle.tar.gz'
    build.build_bundle(source, bundle)
    with pytest.raises(ValueError, match='checksum'):
        build.extract_bundle(bundle, '0' * 64, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


def test_verified_bundle_extracts_and_refuses_existing_destination(source, tmp_path):
    build = tool('build_bundle')
    bundle = tmp_path / 'bundle.tar.gz'
    build.build_bundle(source, bundle)
    sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    release = tmp_path / 'release'
    result = build.extract_bundle(bundle, sha, release)
    assert len(result['commit']) == 40
    assert (release / 'src/onpf/app.py').read_bytes() == b'# code\n'
    with pytest.raises((ValueError, FileExistsError)):
        build.extract_bundle(bundle, sha, release)


def test_bundle_keeps_exact_public_readme_targets_without_private_examples(source, tmp_path):
    public = {
        'THIRD_PARTY_NOTICES.md': b'Public third-party notices\n',
        'CONTRIBUTING.md': b'Public contribution instructions\n',
        'examples/fictional-group-input.json': b'{"fictional": true}\n',
    }
    for name, data in {**public, 'examples/private-participant-input.json': b'PRIVATE'}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.org', 'commit', '-m', 'public references'],
                   check=True, capture_output=True)
    build = tool('build_bundle')
    bundle = tmp_path / 'public.tar.gz'
    build.build_bundle(source, bundle)
    with tarfile.open(bundle) as archive:
        for name, data in public.items():
            assert name in archive.getnames(), f'Missing public README target: {name}'
            assert archive.extractfile(name).read() == data
        assert 'examples/private-participant-input.json' not in archive.getnames()


def test_gateway_bundle_contains_runtime_artifacts_and_excludes_live_keys(source, tmp_path):
    aws = ROOT / 'deploy/aws'
    names = ('setup_ai_gateway.py', 'launch_ai_gateway.py', 'gateway_asgi.py', 'ai-gateway.example.json',
             'gateway-requirements.in', 'gateway-requirements-linux-py312.lock',
             'gateway-lock-metadata.json', 'templates/onpf-ai-gateway.service',
             'templates/ai-gateway.yaml.example', 'templates/ai-gateway.env.example')
    expected = {}
    for name in names:
        path = source / 'deploy/aws' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        expected['deploy/aws/' + name] = (aws / name).read_bytes()
        path.write_bytes(expected['deploy/aws/' + name])
    private = ('gateway.env', 'production.env', 'ai-drafting.env', 'master-key',
               'provider-key', 'ai-gateway-key', 'operator.json')
    for name in private:
        (source / 'deploy/aws' / name).write_text('SYNTHETIC_PRIVATE_SENTINEL')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.org', 'commit', '-m', 'gateway artifacts'],
                   check=True, capture_output=True)
    path = tmp_path / 'gateway.tar.gz'
    tool('build_bundle').build_bundle(source, path)
    with tarfile.open(path) as archive:
        for name, data in expected.items():
            # Compare the committed blob; Git can normalize example line endings.
            blob = subprocess.run(['git', '-C', str(source), 'show', 'HEAD:' + name],
                                  check=True, capture_output=True).stdout
            assert archive.extractfile(name).read() == blob
        for name in private:
            assert 'deploy/aws/' + name not in archive.getnames()


@pytest.fixture
def config(tmp_path, monkeypatch):
    from onpf.app import create_app
    root = tmp_path / 'data'
    root.mkdir()
    (root / '.onpf-volume-id').write_text('vol-0123456789abcdef0\n')
    (root / '.onpf-volume-id').chmod(0o600)
    instance = root / 'instance'
    create_app({'INSTANCE_PATH': str(instance), 'DATABASE': str(instance / 'onpf.sqlite3')})
    monkeypatch.setattr(os.path, 'ismount', lambda path: Path(path) == root)
    return {'public_host': 'onpf.example.org', 'volume_id': 'vol-0123456789abcdef0',
            'data_root': str(root), 'instance': str(instance), 'database': str(instance / 'onpf.sqlite3'),
            'staging': str(root / 'backups'), 'bucket': 'example-backup-bucket', 'prefix': 'backups/',
            'region': 'ca-central-1', 'stack': 'onpf-test', 'local_keep': 2}


def test_initialize_executes_real_administration_cli_into_new_storage(config, monkeypatch):
    install = tool('install')
    original_database = Path(config['database'])
    original_bytes = original_database.read_bytes()
    fresh = Path(config['data_root']) / 'fresh-instance'
    fresh.mkdir(mode=0o700)
    selected = dict(config, instance=str(fresh), database=str(fresh / 'onpf.sqlite3'))

    def real_cli_without_linux_user_switch(args):
        # Model only runuser/the installed interpreter path. Execute the actual
        # production-selected module and arguments in a separate Python process.
        boundary = args.index('--')
        result = subprocess.run([sys.executable, *args[boundary + 2:]], check=True,
                                capture_output=True, text=True,
                                env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
        return result.stdout.strip()

    monkeypatch.setattr(install, 'command', real_cli_without_linux_user_switch)
    install.initialize_database(selected)
    assert (fresh / 'onpf.sqlite3').is_file()
    assert (fresh / '.secret').is_file()
    with sqlite3.connect(fresh / 'onpf.sqlite3') as connection:
        assert connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT count(*) FROM users').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] > 0
    assert original_database.read_bytes() == original_bytes


@pytest.mark.parametrize('value', ['bad;touch /tmp/x', 'https://example.org', 'evil\nserver {}', '../example.org'])
def test_configuration_refuses_hostname_injection(config, value):
    install = tool('install')
    config['public_host'] = value
    with pytest.raises(ValueError):
        install.validate_config(config)


@pytest.mark.parametrize('field,value', [('data_root', 'relative'), ('instance', '/tmp/outside'),
                                         ('staging', '/tmp/../bad'), ('bucket', 'bucket;touch x'),
                                         ('prefix', '../backups/'), ('stack', 'bad\nname')])
def test_configuration_refuses_unsafe_paths_and_aws_values(config, field, value):
    install = tool('install')
    config[field] = value
    with pytest.raises(ValueError):
        install.validate_config(config)


def test_contact_setup_settings_flow_into_private_service_environment(config):
    install = tool('install')
    config.update(contact_to='admin@example.org', smtp_host='smtp.example.org', smtp_port=587,
                  smtp_from='sender@example.org', smtp_user='sender@example.org',
                  smtp_password_file='/etc/onpf/smtp-password')
    validated = install.validate_config(config)
    environment = install.environment(validated)
    assert environment['ONPF_CONTACT_TO'] == 'admin@example.org'
    assert environment['ONPF_SMTP_HOST'] == 'smtp.example.org'
    assert environment['ONPF_SMTP_PORT'] == '587'
    assert environment['ONPF_SMTP_FROM'] == 'sender@example.org'
    assert environment['ONPF_SMTP_USER'] == 'sender@example.org'
    assert environment['ONPF_SMTP_PASSWORD_FILE'] == '/etc/onpf/smtp-password'
    assert 'ONPF_SMTP_PASSWORD' not in environment


def test_legacy_ai_settings_require_separate_gateway_configuration(config):
    install = tool('install')
    config.update(ai_base_url='https://provider.example/v1', ai_model='fictional-model',
                  ai_api_key_file='/etc/onpf/ai-api-key', ai_requests_per_day=12)
    with pytest.raises(ValueError, match='separate gateway'):
        install.validate_config(config)


@pytest.mark.parametrize('changes', [
    {'ai_base_url': 'https://provider.example/v1\nBAD=value'},
    {'ai_model': 'model\nBAD=value'}, {'ai_api_key': 'private-key'},
    {'ai_api_key_file': '/tmp/key'}, {'ai_requests_per_day': 0},
])
def test_ai_installer_rejects_unsafe_configuration(config, changes):
    install = tool('install')
    config.update(ai_base_url='https://provider.example/v1', ai_model='fictional',
                  ai_api_key_file='/etc/onpf/ai-api-key')
    config.update(changes)
    with pytest.raises(ValueError):
        install.validate_config(config)


@pytest.mark.parametrize('field,value', [
    ('contact_to', 'person@example.org\nBAD=value'),
    ('smtp_host', 'smtp.example.org\nBAD=value'),
    ('smtp_port', 0),
    ('smtp_from', 'not-an-email'),
    ('smtp_user', 'user\nBAD=value'),
    ('smtp_password_file', '/tmp/unreviewed-secret'),
])
def test_contact_setup_rejects_invalid_environment_values(config, field, value):
    install = tool('install')
    config[field] = value
    with pytest.raises(ValueError):
        install.validate_config(config)


@pytest.mark.parametrize('failure', ['unmounted', 'wrong-marker', 'missing-marker'])
def test_backup_refuses_bad_storage_without_creating_staging(config, monkeypatch, failure):
    backup = tool('backup')
    if failure == 'unmounted':
        monkeypatch.setattr(os.path, 'ismount', lambda path: False)
    else:
        marker = Path(config['data_root']) / '.onpf-volume-id'
        marker.unlink()
        if failure == 'wrong-marker': marker.write_text('vol-00000000000000000')
    with pytest.raises(ValueError):
        backup.run_backup(config)
    assert not Path(config['staging']).exists()


def test_initialize_refuses_existing_database_without_modifying_it(config):
    install = tool('install')
    database = Path(config['database'])
    before = database.read_bytes()
    with pytest.raises(ValueError, match='existing'):
        install.initialize_database(config)
    assert database.read_bytes() == before


def test_storage_identity_rejects_ambiguous_used_and_wrong_devices():
    install = tool('install')
    disk = {'path': '/dev/nvme1n1', 'type': 'disk', 'serial': 'vol0123456789abcdef0',
            'fstype': None, 'mountpoints': [None]}
    assert install.identify_blank_device({'blockdevices': [disk]}, 'vol-0123456789abcdef0', '/dev/nvme1n1') == '/dev/nvme1n1'
    for disks in ([dict(disk, serial='volwrong')], [disk, dict(disk, path='/dev/nvme2n1')],
                  [dict(disk, fstype='ext4')], [dict(disk, mountpoints=['/'])],
                  [dict(disk, children=[{'path': '/dev/nvme1n1p1'}])]):
        with pytest.raises(ValueError):
            install.identify_blank_device({'blockdevices': disks}, 'vol-0123456789abcdef0', '/dev/nvme1n1')


def fake_aws(tmp_path, monkeypatch, mode):
    """A real child process implements the external CLI protocol for failure injection."""
    backup = sys.modules['backup']
    if not backup.RELEASE_MANIFEST.exists():
        manifest = tmp_path / 'test-release.json'
        manifest.write_text(json.dumps({'commit': '2' * 40}))
        monkeypatch.setattr(backup, 'RELEASE_MANIFEST', manifest)
    script = tmp_path / 'fake_aws.py'
    script.write_text('''import json, os, sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['AWS_CAPTURE'],'a') as f: f.write(json.dumps(a)+'\\n')
if a[:2] == ['s3api','put-object']:
    assert Path(a[a.index('--body')+1]).is_file()
    if os.environ['AWS_MODE']=='fail': sys.exit(9)
    checksum=a[a.index('--checksum-sha256')+1]
    print(json.dumps({'ChecksumSHA256': checksum if os.environ['AWS_MODE']=='ok' else 'incorrect', 'ETag':'"fixture"', 'VersionId':'fixture-version'}))
else:
    assert a[:2] == ['cloudwatch','put-metric-data']
    print('{}')
''')
    capture = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('AWS_CAPTURE', str(capture))
    monkeypatch.setenv('AWS_MODE', mode)
    return [sys.executable, str(script)], capture


@pytest.mark.parametrize('mode', ['fail', 'bad-checksum'])
def test_upload_failure_preserves_staged_copy_and_heartbeat(config, tmp_path, monkeypatch, mode):
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, mode)
    staging = Path(config['staging']); staging.mkdir()
    heartbeat = staging / 'last-success.json'
    heartbeat.write_text('{"uploaded_at":1}')
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        backup.run_backup(config, aws=aws)
    assert heartbeat.read_text() == '{"uploaded_at":1}'
    archives = list(staging.glob('*.private.json'))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text())['classification'] == 'PRIVATE'
    assert len(capture.read_text().splitlines()) == 1


def test_successful_backup_restores_real_data_and_only_then_emits_metric(config, tmp_path, monkeypatch):
    from onpf.archives.service import restore_private
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, 'ok')
    with sqlite3.connect(config['database']) as db:
        db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES ('12345678-1234-4234-8234-123456789abc','fixture-user','fixture-hash','2026-01-01T00:00:00+00:00')")
    result = backup.run_backup(config, aws=aws)
    archive = Path(result['archive'])
    restored = tmp_path / 'restored.sqlite3'
    restore_private(archive, restored)
    with sqlite3.connect(restored) as db:
        assert db.execute('SELECT username FROM users').fetchone() == ('fixture-user',)
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    upload, metrics = calls
    assert upload[upload.index('--key') + 1].startswith('backups/')
    assert upload[upload.index('--checksum-sha256') + 1] == base64.b64encode(hashlib.sha256(archive.read_bytes()).digest()).decode()
    metric = json.loads(metrics[metrics.index('--metric-data') + 1])[0]
    assert metric == {'MetricName': 'BackupSuccess', 'Dimensions': [{'Name': 'StackName', 'Value': 'onpf-test'}], 'Value': 1, 'Unit': 'Count'}
    assert json.loads((archive.parent / 'last-success.json').read_text())['key'] == result['key']


def test_backup_overlap_refused_and_retention_bounded_without_deleting_failed_copy(config, tmp_path, monkeypatch):
    backup = tool('backup')
    aws, _ = fake_aws(tmp_path, monkeypatch, 'ok')
    staging = Path(config['staging']); staging.mkdir()
    with backup.backup_lock(staging):
        with pytest.raises(ValueError, match='running'):
            backup.run_backup(config, aws=aws)
    for _ in range(4): backup.run_backup(config, aws=aws)
    assert len(list(staging.glob('*.private.json'))) == 2
    monkeypatch.setenv('AWS_MODE', 'fail')
    with pytest.raises(subprocess.CalledProcessError): backup.run_backup(config, aws=aws)
    assert len(list(staging.glob('*.private.json'))) == 3
    monkeypatch.setenv('AWS_MODE', 'ok')
    backup.run_backup(config, aws=aws)
    assert len(list(staging.glob('*.private.json'))) == 2  # failed archive retried, then bounded cleanup


def test_monitor_reports_missing_mount_without_writing_state(config, tmp_path, monkeypatch):
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, 'ok')
    monkeypatch.setattr(os.path, 'ismount', lambda path: False)
    backup.monitor(config, aws=aws)
    args = json.loads(capture.read_text().splitlines()[0])
    metrics = json.loads(args[args.index('--metric-data') + 1])
    assert {m['MetricName']: m['Value'] for m in metrics} == {
        'ApplicationHealthy': 0, 'DataDiskUsedPercent': 100, 'BackupAgeHours': 1000000}
    assert not Path(config['staging']).exists()


def test_bundle_rejects_a_committed_symlink_without_needing_windows_symlink_privilege(source, tmp_path):
    build = tool('build_bundle')
    blob = subprocess.run(['git', '-C', str(source), 'hash-object', '-w', '--stdin'],
                          input=b'../../instance/private.json', check=True, capture_output=True).stdout.decode().strip()
    subprocess.run(['git', '-C', str(source), 'update-index', '--add', '--cacheinfo', f'120000,{blob},src/onpf/link'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'symlink'], check=True, capture_output=True)
    with pytest.raises(ValueError, match='link'):
        build.build_bundle(source, tmp_path / 'unsafe.tar.gz')
    assert not (tmp_path / 'unsafe.tar.gz').exists()


def test_repeated_failed_uploads_retry_identical_archive_without_filling_disk(config, tmp_path, monkeypatch):
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, 'fail')
    for _ in range(3):
        with pytest.raises(subprocess.CalledProcessError): backup.run_backup(config, aws=aws)
    assert len(list(Path(config['staging']).glob('*.private.json'))) == 1
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len({call[call.index('--key') + 1] for call in calls}) == 1
    assert not (Path(config['staging']) / 'last-success.json').exists()


def test_backup_records_exact_deployed_release_for_recovery(config, tmp_path, monkeypatch):
    backup = tool('backup')
    release = tmp_path / 'release.json'
    release.write_text(json.dumps({'format': 1, 'commit': '1' * 40, 'files': {}}))
    monkeypatch.setattr(backup, 'RELEASE_MANIFEST', release, raising=False)
    aws, capture = fake_aws(tmp_path, monkeypatch, 'ok')
    result = backup.run_backup(config, aws=aws)
    receipt = json.loads((Path(config['staging']) / 'last-success.json').read_text())
    assert receipt['release_commit'] == '1' * 40
    call = json.loads(capture.read_text().splitlines()[0])
    assert json.loads(call[call.index('--metadata') + 1]) == {'onpf-release-commit': '1' * 40}
    assert Path(result['archive']).exists()


def test_upgrade_refuses_running_service_or_unknown_systemd_state_before_extraction(tmp_path, monkeypatch):
    install = tool('install')
    invoked = []
    def running(args, **kwargs):
        invoked.append(args)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(subprocess, 'run', running)
    monkeypatch.setattr(install, 'CODE', tmp_path / 'code')
    with pytest.raises(ValueError, match='Stop'):
        install.install_release(tmp_path / 'nonexistent.tar', 'a' * 64)
    assert not (tmp_path / 'code').exists()
    assert invoked == [['/usr/bin/systemctl', 'is-active', '--quiet', 'onpf.service']]
    monkeypatch.setattr(subprocess, 'run', lambda args, **kwargs: subprocess.CompletedProcess(args, 1))
    with pytest.raises(ValueError, match='establish'):
        install.require_stopped()


def test_monitor_emits_stale_age_after_success_ages(config, tmp_path, monkeypatch):
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, 'ok')
    staging = Path(config['staging']); staging.mkdir()
    (staging / 'last-success.json').write_text('{"uploaded_at":1000}')
    monkeypatch.setattr(backup.time, 'time', lambda: 1000 + 31 * 3600)
    monkeypatch.setattr(backup, 'application_healthy', lambda host: 1)
    backup.monitor(config, aws=aws)
    call = json.loads(capture.read_text().splitlines()[0])
    metrics = {item['MetricName']: item for item in json.loads(call[call.index('--metric-data') + 1])}
    assert metrics['BackupAgeHours']['Value'] == 31
    assert metrics['BackupAgeHours']['Unit'] == 'None'
    assert metrics['ApplicationHealthy']['Value'] == 1
    assert 0 <= metrics['DataDiskUsedPercent']['Value'] <= 100


def test_real_release_contains_importable_archive_and_export_packages(tmp_path):
    build = tool('build_bundle')
    bundle = tmp_path / 'real-source.tar.gz'
    build.build_bundle(ROOT, bundle)
    release = tmp_path / 'release'
    build.extract_bundle(bundle, hashlib.sha256(bundle.read_bytes()).hexdigest(), release)
    for package, module in (('archives', 'service.py'), ('exports', 'routes.py')):
        assert (release / 'src/onpf' / package / module).is_file()
    # Isolated interpreter (-I) discards workspace/PYTHONPATH imports. Explicitly
    # prepend extracted source and confirm both package origins are in that tree.
    code = '''import pathlib, sys
sys.path.insert(0, sys.argv[1])
import onpf.archives.service, onpf.exports.routes
for module in (onpf.archives.service, onpf.exports.routes):
    assert pathlib.Path(module.__file__).is_relative_to(pathlib.Path(sys.argv[1]))
'''
    subprocess.run([sys.executable, '-I', '-c', code, str(release / 'src')], check=True, capture_output=True)


def test_config_directory_effective_access_despite_private_umask(tmp_path, monkeypatch):
    install = tool('install')
    config_dir = tmp_path / 'etc/onpf'
    acme = tmp_path / 'var/www/onpf-acme'
    monkeypatch.setattr(install, 'ETC', config_dir)
    monkeypatch.setattr(install, 'ACME', acme, raising=False)
    assert hasattr(install, 'prepare_config_directories'), 'Final directory permission preparation is missing'
    real_mkdir, real_chmod = Path.mkdir, Path.chmod
    modes, owners = {}, {}
    # Model the Unix permission boundary on Windows; retain real filesystem
    # creation. A mkdir request alone gets 0077 masked and fails access checks.
    def mkdir(path, mode=0o777, parents=False, exist_ok=False):
        was_present = path.exists()
        result = real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)
        if not was_present: modes[path] = mode & ~0o077
        return result
    def chmod(path, mode, **kwargs):
        modes[path] = mode
        return real_chmod(path, mode, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    monkeypatch.setattr(Path, 'chmod', chmod)
    monkeypatch.setattr(install.shutil, 'chown', lambda path, user=None, group=None: owners.update({Path(path): (user, group)}))
    previous = os.umask(0o077)
    try: install.prepare_config_directories()
    finally: os.umask(previous)
    assert modes[config_dir] == 0o750 and owners[config_dir] == ('root', 'onpf')
    assert modes[config_dir] & 0o050 == 0o050  # onpf group can read/traverse
    for directory in (tmp_path / 'etc', tmp_path / 'var', acme.parent, acme):
        assert modes[directory] == 0o755
        assert modes[directory] & 0o005 == 0o005  # unrelated nginx worker can read/traverse


@pytest.mark.parametrize('fresh_failure', [None, 'snapshot', 'upload'])
def test_recovered_pending_upload_requires_fresh_snapshot_before_clearing_staleness(config, tmp_path, monkeypatch, fresh_failure):
    from onpf.archives import service
    backup = tool('backup')
    aws, capture = fake_aws(tmp_path, monkeypatch, 'fail')
    staging = Path(config['staging']); staging.mkdir()
    heartbeat = staging / 'last-success.json'
    heartbeat.write_text('{"uploaded_at":1}')
    with pytest.raises(subprocess.CalledProcessError): backup.run_backup(config, aws=aws)
    original = next(staging.glob('*.private.json'))
    original_bytes = original.read_bytes()
    old_commit = json.loads(original.with_suffix('.json.source.json').read_text())['release_commit']
    backup.RELEASE_MANIFEST.write_text(json.dumps({'commit': '3' * 40}))
    with sqlite3.connect(config['database']) as db:
        db.execute("INSERT INTO users(id,username,password_hash,created_at) VALUES ('12345678-1234-4234-8234-123456789abc','newer-user','fixture-hash','2026-01-01T00:00:00+00:00')")
    monkeypatch.setenv('AWS_MODE', 'ok')
    snapshot = service.backup_private
    def capture_fresh(database, destination):
        if fresh_failure == 'snapshot': raise OSError('controlled snapshot failure')
        result = snapshot(database, destination)
        if fresh_failure == 'upload': monkeypatch.setenv('AWS_MODE', 'fail')
        return result
    monkeypatch.setattr(service, 'backup_private', capture_fresh)
    if fresh_failure:
        with pytest.raises((OSError, subprocess.CalledProcessError)): backup.run_backup(config, aws=aws)
        assert heartbeat.read_text() == '{"uploaded_at":1}'
        monkeypatch.setenv('AWS_MODE', 'ok')
        backup.monitor(config, aws=aws)
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        metric_payload = json.loads(calls[-1][calls[-1].index('--metric-data') + 1])
        assert next(item for item in metric_payload if item['MetricName'] == 'BackupAgeHours')['Value'] == 1000000
        assert not any('BackupSuccess' in json.dumps(call) for call in calls)
    else:
        result = backup.run_backup(config, aws=aws)
        restored = tmp_path / 'freshly-restored.sqlite3'
        service.restore_private(Path(result['archive']), restored)
        with sqlite3.connect(restored) as db:
            assert db.execute('SELECT username FROM users').fetchone() == ('newer-user',)
        assert json.loads(heartbeat.read_text())['release_commit'] == '3' * 40
        calls = [json.loads(line) for line in capture.read_text().splitlines()]
        assert sum(call[:2] == ['s3api', 'put-object'] for call in calls) == 3  # failed original, retry, fresh
    assert original.read_bytes() == original_bytes
    assert json.loads(backup.receipt_path(original).read_text())['release_commit'] == old_commit

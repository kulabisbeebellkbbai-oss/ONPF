"""Private logical backup, verified single-PUT upload, and safe operational metrics."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from install import absolute_path, validate_config, validate_storage

AWS = ['/usr/local/bin/aws']
RELEASE_MANIFEST = Path(__file__).resolve().parents[2] / 'release.json'


@contextmanager
def backup_lock(staging):
    """OS lock releases on process death; the lock file is never unlinked."""
    path = absolute_path(str(Path(staging) / '.backup.lock'))
    with path.open('a+b') as stream:
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('A backup is already running.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def aws_call(args, aws):
    # stderr may contain object paths; callers emit only a generic failure event.
    result = subprocess.run([*aws, *args, '--output', 'json', '--no-cli-pager'],
                            check=True, capture_output=True, text=True, timeout=900)
    return json.loads(result.stdout or '{}')


def metrics(config, values, aws):
    payload = [{'MetricName': name, 'Dimensions': [{'Name': 'StackName', 'Value': config['stack']}],
                'Value': value, 'Unit': unit} for name, value, unit in values]
    aws_call(['cloudwatch', 'put-metric-data', '--namespace', 'ONPF/Operations',
              '--metric-data', json.dumps(payload), '--region', config['region']], aws)


def atomic_json(path, value):
    path = absolute_path(str(path))
    pending = path.with_name('.' + path.name + '.' + uuid4().hex)
    with pending.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    pending.chmod(0o600)
    pending.replace(path)


def checksum(path):
    with path.open('rb') as stream:
        return base64.b64encode(hashlib.file_digest(stream, 'sha256').digest()).decode()


def receipt_path(archive):
    return archive.with_suffix(archive.suffix + '.uploaded.json')


def capture_snapshot(config, staging):
    if shutil.disk_usage(staging).free < Path(config['database']).stat().st_size * 3 + 100 * 1024 * 1024:
        raise ValueError('Insufficient free space for a private logical backup.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    archive = staging / (stamp + '-' + uuid4().hex + '.private.json')
    release_commit = json.loads(RELEASE_MANIFEST.read_text())['commit']
    if not re.fullmatch('[0-9a-f]{40}', release_commit):
        raise ValueError('The deployed release identity is invalid.')
    from onpf.archives.service import backup_private
    backup_private(Path(config['database']), archive)
    atomic_json(archive.with_suffix('.json.source.json'), {'release_commit': release_commit})
    return archive


def upload_archive(config, archive, aws):
    archive.chmod(0o600)
    if archive.stat().st_size >= 5 * 1024 ** 3:
        raise ValueError('Archive exceeds the supported single-PUT limit.')
    sha = checksum(archive)
    release_commit = json.loads(archive.with_suffix('.json.source.json').read_text())['release_commit']
    if not re.fullmatch('[0-9a-f]{40}', release_commit):
        raise ValueError('Staged archive release identity is invalid.')
    key = config['prefix'] + archive.name
    response = aws_call(['s3api', 'put-object', '--bucket', config['bucket'], '--key', key,
                         '--body', str(archive), '--checksum-algorithm', 'SHA256',
                         '--checksum-sha256', sha, '--server-side-encryption', 'AES256',
                         '--metadata', json.dumps({'onpf-release-commit': release_commit}),
                         '--region', config['region']], aws)
    if response.get('ChecksumSHA256') != sha:
        raise ValueError('S3 did not confirm the expected completed object checksum.')
    receipt = {'uploaded_at': time.time(), 'key': key, 'checksum_sha256': sha,
               'version_id': response.get('VersionId'), 'archive': str(archive), 'release_commit': release_commit}
    atomic_json(receipt_path(archive), receipt)
    return receipt


def prune_uploaded(staging, keep):
    confirmed = sorted((path for path in staging.glob('*.private.json') if receipt_path(path).is_file()),
                       key=lambda path: path.name, reverse=True)
    for old in confirmed[keep:]:
        receipt = json.loads(receipt_path(old).read_text())
        if receipt.get('checksum_sha256') != checksum(old):
            raise ValueError('Staged archive integrity changed; cleanup refused.')
        old.unlink(); receipt_path(old).unlink(); old.with_suffix('.json.source.json').unlink()


def run_backup(config, aws=None):
    config = validate_storage(config)
    aws = AWS if aws is None else aws
    staging = absolute_path(config['staging'])
    staging.mkdir(mode=0o700, exist_ok=True)
    with backup_lock(staging):
        for path in staging.iterdir(): absolute_path(str(path))
        # At most one retry plus one fresh snapshot. A recovered old archive alone
        # cannot clear freshness monitoring or advance the completion heartbeat.
        pending = [path for path in staging.glob('*.private.json') if not receipt_path(path).exists()]
        if len(pending) > 1:
            raise ValueError('Multiple pending archives need operator inspection.')
        recovery = staging / 'recovery-pending.json'
        if pending:
            atomic_json(recovery, {'started_at': time.time()})
            upload_archive(config, pending[0], aws)
            prune_uploaded(staging, config['local_keep'])
        archive = capture_snapshot(config, staging)
        receipt = upload_archive(config, archive, aws)
        prune_uploaded(staging, config['local_keep'])
        atomic_json(staging / 'last-success.json', receipt)
        recovery.unlink(missing_ok=True)
        metrics(config, [('BackupSuccess', 1, 'Count')], aws)
        return {'archive': str(archive), 'key': receipt['key']}


def application_healthy(host):
    connection = http.client.HTTPConnection('127.0.0.1', 8765, timeout=5)
    try:
        connection.request('GET', '/login', headers={'Host': host, 'X-Forwarded-Proto': 'https',
                                                   'X-Forwarded-For': '127.0.0.1'})
        response = connection.getresponse()
        return int(response.status == 200)
    except (OSError, http.client.HTTPException):
        return 0
    finally:
        connection.close()


def monitor(config, aws=None):
    config = validate_config(config)
    aws = AWS if aws is None else aws
    healthy, used, age = 0, 100, 1000000
    try:
        validate_storage(config)
        usage = shutil.disk_usage(config['data_root'])
        used = 100 * usage.used / usage.total
        healthy = application_healthy(config['public_host'])
        heartbeat = absolute_path(str(Path(config['staging']) / 'last-success.json'))
        recovery = absolute_path(str(Path(config['staging']) / 'recovery-pending.json'))
        if heartbeat.is_file() and not recovery.exists():
            stamp = json.loads(heartbeat.read_text())['uploaded_at']
            if type(stamp) in (int, float) and 0 < stamp <= time.time():
                age = (time.time() - stamp) / 3600
    except (ValueError, OSError, KeyError, TypeError):
        pass  # publish safe failure values; never mount, initialize or log content
    metrics(config, [('ApplicationHealthy', healthy, 'Count'), ('DataDiskUsedPercent', used, 'Percent'),
                     ('BackupAgeHours', age, 'None')], aws)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--monitor', action='store_true', help='Publish one safe health/disk/backup-age sample')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text())
        (monitor if args.monitor else run_backup)(config)
    except Exception:
        # No request data, tokens, archive bodies, object keys or raw AWS errors.
        print('ONPF operational job failed; operator inspection is required.', file=sys.stderr)
        return 1
    print('ONPF operational job completed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

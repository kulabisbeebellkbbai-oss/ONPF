"""Explicit Ubuntu installation steps; none run on import or ordinary startup."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

DATA = Path('/var/lib/onpf')
CODE = Path('/opt/onpf')
ETC = Path('/etc/onpf')
ACME = Path('/var/www/onpf-acme')
TEMPLATES = Path(__file__).parent / 'templates'


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def absolute_path(value):
    if not isinstance(value, str) or any(c in value for c in '\x00\r\n;`$"\'{}'):
        raise ValueError('Invalid absolute path.')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path.resolve() != path:
        raise ValueError('Paths must be absolute without traversal or symlink redirects.')
    return path


def validate_config(config):
    config = dict(config)
    if 'ai_api_key' in config or any(config.get(name) for name in ('ai_base_url', 'ai_model', 'ai_api_key_file')):
        raise ValueError('Legacy AI configuration requires the separate gateway setup. Keep provider credentials outside application settings.')
    host = config.get('public_host', '')
    if (not isinstance(host, str) or len(host) > 253 or '.' not in host
            or re.fullmatch(r'[0-9.]+', host)
            or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in host.split('.'))):
        raise ValueError('Choose a canonical public DNS hostname.')
    config['public_host'] = host.lower()
    contact_to = config.get('contact_to', '')
    smtp_from = config.get('smtp_from', '')
    for field, value in (('contact_to', contact_to), ('smtp_from', smtp_from)):
        if value and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}', value)):
            raise ValueError('Invalid deployment configuration: ' + field)
    smtp_host = config.get('smtp_host', '')
    if smtp_host and (not isinstance(smtp_host, str) or len(smtp_host) > 253 or
                      any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                          for label in smtp_host.split('.'))):
        raise ValueError('Invalid deployment configuration: smtp_host')
    smtp_user = config.get('smtp_user', '')
    if not isinstance(smtp_user, str) or len(smtp_user) > 254 or (smtp_user and not re.fullmatch(r'[A-Za-z0-9@._%+-]+', smtp_user)):
        raise ValueError('Invalid deployment configuration: smtp_user')
    smtp_port = config.get('smtp_port', 587)
    if type(smtp_port) is not int or not 1 <= smtp_port <= 65535:
        raise ValueError('Invalid deployment configuration: smtp_port')
    secret_file = config.get('smtp_password_file', '')
    if secret_file not in ('', '/etc/onpf/smtp-password'):
        raise ValueError('Invalid deployment configuration: smtp_password_file')
    if smtp_host and not smtp_from:
        raise ValueError('smtp_from is required when smtp_host is configured.')
    if smtp_user and not secret_file:
        raise ValueError('smtp_password_file is required when smtp_user is configured.')
    for field, pattern in {'volume_id': r'vol-(?:[0-9a-f]{8}|[0-9a-f]{17})',
                           'bucket': r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',
                           'prefix': r'backups/(?:[a-zA-Z0-9_-]+/)*',
                           'region': r'[a-z]{2}-[a-z]+-[1-9]',
                           'stack': r'[a-zA-Z][a-zA-Z0-9-]{0,127}'}.items():
        if not isinstance(config.get(field), str) or not re.fullmatch(pattern, config[field]):
            raise ValueError('Invalid deployment configuration: ' + field)
    root = absolute_path(config['data_root'])
    for field in ('instance', 'database', 'staging'):
        path = absolute_path(config[field])
        if path == root or not path.is_relative_to(root):
            raise ValueError('Private paths must stay below the data root.')
    if Path(config['database']).parent != Path(config['instance']):
        raise ValueError('The database must be directly inside the private instance.')
    staging = Path(config['staging'])
    instance = Path(config['instance'])
    if staging == instance or staging.is_relative_to(instance) or instance.is_relative_to(staging):
        raise ValueError('Backup staging must be separate from the instance.')
    keep = config.get('local_keep', 3)
    if type(keep) is not int or not 1 <= keep <= 30:
        raise ValueError('local_keep must be between 1 and 30.')
    config['local_keep'] = keep
    # Integer-only bounds prevent Nginx configuration injection and accidental
    # removal of the proxy boundary. Defaults suit a small pilot; tune shared NAT.
    for field, default, maximum in (
            ('request_rate_per_second', 10, 100), ('request_burst', 40, 500),
            ('login_rate_per_minute', 6, 60), ('login_burst', 5, 30)):
        value = config.get(field, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{field} must be an integer between 1 and {maximum}.')
        config[field] = value
    return config


def environment(config):
    result = dict(ONPF_PUBLIC_HOST=config['public_host'], ONPF_VOLUME_ID=config['volume_id'],
                ONPF_DATA_ROOT=config['data_root'], ONPF_INSTANCE_PATH=config['instance'],
                ONPF_DATABASE=config['database'])
    for source, target in (('contact_to', 'ONPF_CONTACT_TO'), ('smtp_host', 'ONPF_SMTP_HOST'),
                           ('smtp_from', 'ONPF_SMTP_FROM'), ('smtp_user', 'ONPF_SMTP_USER'),
                           ('smtp_password_file', 'ONPF_SMTP_PASSWORD_FILE')):
        if config.get(source):
            result[target] = config[source]
    if config.get('smtp_host'):
        result['ONPF_SMTP_PORT'] = str(config.get('smtp_port', 587))
    return result


def validate_storage(config, initialized=True):
    config = validate_config(config)
    root = Path(config['data_root'])
    marker = absolute_path(str(root / '.onpf-volume-id'))
    if not root.is_dir() or not os.path.ismount(root):
        raise ValueError('The intended private data volume is not mounted.')
    if (not marker.is_file() or marker.stat().st_size > 80
            or (os.name != 'nt' and marker.stat().st_mode & 0o077)
            or marker.read_text().strip() != config['volume_id']):
        raise ValueError('The private data volume identity marker does not match.')
    if initialized:
        from onpf.production import load_config
        load_config(environment(config))
    return config


def identify_blank_device(listing, volume_id, device):
    if not re.fullmatch(r'vol-(?:[0-9a-f]{8}|[0-9a-f]{17})', volume_id):
        raise ValueError('Invalid intended EBS volume identity.')
    matches = [disk for disk in listing['blockdevices']
               if str(disk.get('serial', '')).replace('-', '') == volume_id.replace('-', '')]
    if len(matches) != 1:
        raise ValueError('The intended EBS device identity is missing or ambiguous.')
    disk = matches[0]
    if (disk.get('path') != device or disk.get('type') != 'disk' or disk.get('fstype')
            or disk.get('children') or any(disk.get('mountpoints') or [])):
        raise ValueError('Initial storage setup requires the identified blank, unmounted whole disk.')
    return device


def service_user():
    result = subprocess.run(['/usr/bin/id', '-u', 'onpf'], capture_output=True)
    if result.returncode:
        command(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', '/nonexistent',
                 '--shell', '/usr/sbin/nologin', 'onpf'])


def prepare_storage(volume_id, device):
    absolute_path(str(DATA))
    device = str(absolute_path(device))
    if not device.startswith('/dev/') or not stat.S_ISBLK(os.stat(device).st_mode):
        raise ValueError('Choose an attached block device.')
    listing = json.loads(command(['/usr/bin/lsblk', '--json', '--paths', '--output', 'PATH,TYPE,SERIAL,FSTYPE,MOUNTPOINTS']))
    identify_blank_device(listing, volume_id, device)
    signatures = json.loads(command(['/usr/sbin/wipefs', '--json', device])).get('signatures', [])
    if signatures or os.path.ismount(DATA) or (DATA.exists() and any(DATA.iterdir())):
        raise ValueError('Device signatures or existing data mount prevent initial formatting.')
    fstab = Path('/etc/fstab')
    old = fstab.read_text()
    if any(len(line.split()) > 1 and line.split()[1] == str(DATA) for line in old.splitlines() if not line.lstrip().startswith('#')):
        raise ValueError('An existing data mount configuration must be reviewed manually.')
    service_user()
    DATA.mkdir(mode=0o750, parents=True, exist_ok=True)
    command(['/usr/sbin/mkfs.ext4', device])  # no force flag; only explicit blank setup
    uuid = command(['/usr/sbin/blkid', '-s', 'UUID', '-o', 'value', device])
    if not re.fullmatch(r'[0-9a-f-]{36}', uuid):
        raise ValueError('Formatted filesystem UUID could not be verified.')
    fstab.write_text(old.rstrip() + f'\nUUID={uuid} {DATA} ext4 defaults,nodev,nosuid,noexec 0 2\n')
    command(['/usr/bin/systemctl', 'daemon-reload'])
    command(['/usr/bin/mount', str(DATA)])
    observed = json.loads(command(['/usr/bin/findmnt', '--json', '--target', str(DATA), '--output', 'TARGET,UUID,FSTYPE']))['filesystems']
    if observed != [{'target': str(DATA), 'uuid': uuid, 'fstype': 'ext4'}]:
        raise ValueError('Mounted filesystem did not match the intended ext4 UUID.')
    marker = DATA / '.onpf-volume-id'
    with marker.open('x') as stream:
        stream.write(volume_id + '\n')
    shutil.chown(DATA, user='root', group='onpf'); DATA.chmod(0o750)
    shutil.chown(marker, user='onpf', group='onpf'); marker.chmod(0o400)
    for path in (DATA / 'instance', DATA / 'backups'):
        path.mkdir(mode=0o700)
        shutil.chown(path, user='onpf', group='onpf')


def require_stopped():
    for unit in ('onpf.service', 'onpf-backup.service', 'onpf-backup.timer'):
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', unit], capture_output=True)
        if result.returncode == 0:
            raise ValueError('Stop application and backup units before changing a release.')
        if result.returncode not in (3, 4):
            raise ValueError('Could not establish that application and backup units are stopped.')


def install_release(bundle, sha256):
    from build_bundle import extract_bundle
    require_stopped()
    service_user()
    absolute_path(str(CODE))
    CODE.mkdir(mode=0o755, exist_ok=True); CODE.chmod(0o755)
    releases = CODE / 'releases'; releases.mkdir(mode=0o755, exist_ok=True); releases.chmod(0o755)
    release = releases / sha256
    absolute_path(str(release))
    manifest = extract_bundle(bundle, sha256, release)
    # Build trusted source/dependencies as an unprivileged user. Afterwards the
    # service gets read/execute access only; state ownership never touches code.
    for path in [release, *release.rglob('*')]:
        shutil.chown(path, user='onpf', group='onpf')
    command(['/usr/sbin/runuser', '-u', 'onpf', '--', '/usr/bin/python3', '-m', 'venv', str(release / '.venv')])
    python = str(release / '.venv/bin/python')
    command(['/usr/sbin/runuser', '-u', 'onpf', '--', python, '-m', 'pip', 'install', '--disable-pip-version-check',
             '--no-cache-dir', '-r', str(release / 'requirements.lock')])
    command(['/usr/sbin/runuser', '-u', 'onpf', '--', python, '-m', 'pip', 'install', '--disable-pip-version-check',
             '--no-cache-dir', '--no-build-isolation', '--no-deps', str(release)])
    for path in [release, *release.rglob('*')]:
        if not path.is_symlink():
            shutil.chown(path, user='root', group='root')
            path.chmod(0o755 if path.is_dir() else (path.stat().st_mode & 0o755) | 0o444)
    pending = CODE / '.current-next'
    if pending.exists() or pending.is_symlink():
        raise ValueError('A pending release pointer requires operator inspection.')
    pending.symlink_to(release, target_is_directory=True)
    pending.replace(CODE / 'current')
    return manifest


def initialize_database(config):
    config = validate_storage(config, initialized=False)
    if Path(config['database']).exists():
        raise ValueError('Refusing to initialize an existing database.')
    if Path(config['database']).name != 'onpf.sqlite3':
        raise ValueError('Initial CLI setup requires the default database filename.')
    command(['/usr/sbin/runuser', '-u', 'onpf', '--', str(CODE / 'current/.venv/bin/python'),
             '-m', 'onpf.cli', '--instance', config['instance'], 'init'])


def render_template(name, config):
    config = validate_config(config)
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    for field in ('public_host', 'request_rate_per_second', 'request_burst',
                  'login_rate_per_minute', 'login_burst'):
        text = text.replace('@@' + field.upper() + '@@', str(config[field]))
    return text


def write_config(path, text, mode=0o644, group=None):
    if path.is_symlink():
        raise ValueError('Refusing to write configuration through a symlink.')
    path.write_text(text, encoding='utf-8')
    path.chmod(mode)
    if group: shutil.chown(path, user='root', group=group)


def make_directory(path, mode=0o755, group='root'):
    """Set final Unix modes explicitly; mkdir modes are reduced by umask0077."""
    path = absolute_path(str(path))
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for parent in reversed(missing):
        parent.mkdir(mode=0o755)
        shutil.chown(parent, user='root', group='root')
        parent.chmod(0o755)
    path.mkdir(mode=mode, exist_ok=True)
    shutil.chown(path, user='root', group=group)
    path.chmod(mode)


def prepare_config_directories():
    make_directory(ETC, 0o750, 'onpf')
    make_directory(ACME.parent)
    make_directory(ACME)


def configure(config, https=False):
    validate_storage(config, initialized=False)
    service_user()
    prepare_config_directories()
    write_config(ETC / 'production.env', ''.join(f'{key}={value}\n' for key, value in environment(config).items()), 0o600)
    write_config(ETC / 'backup.json', json.dumps(config, indent=2) + '\n', 0o640, 'onpf')
    for name in ('onpf.service', 'onpf-backup.service', 'onpf-backup.timer', 'onpf-monitor.service', 'onpf-monitor.timer'):
        write_config(Path('/etc/systemd/system') / name, render_template(name, config))
    if https:
        for filename in ('fullchain.pem', 'privkey.pem'):
            if not (Path('/etc/letsencrypt/live') / config['public_host'] / filename).is_file():
                raise ValueError('Operator must obtain the domain certificate before HTTPS configuration.')
    write_config(Path('/etc/nginx/sites-available/onpf'), render_template('nginx-https.conf' if https else 'nginx-http.conf', config))
    enabled = Path('/etc/nginx/sites-enabled/onpf')
    if not enabled.exists(): enabled.symlink_to('/etc/nginx/sites-available/onpf')
    if enabled.resolve() != Path('/etc/nginx/sites-available/onpf'):
        raise ValueError('Unexpected enabled Nginx site.')
    hook = Path('/etc/letsencrypt/renewal-hooks/deploy/onpf-reload')
    hook.parent.mkdir(parents=True, exist_ok=True)
    write_config(hook, '#!/bin/sh\nset -eu\n/usr/sbin/nginx -t\n/usr/bin/systemctl reload nginx\n', 0o755)
    command(['/usr/sbin/nginx', '-t'])
    command(['/usr/bin/systemctl', 'daemon-reload'])
    command(['/usr/bin/systemctl', 'enable', '--now', 'nginx', 'certbot.timer'])
    command(['/usr/bin/systemctl', 'reload', 'nginx'])


def start(config):
    # Validation runs as the real service user before systemd can start it.
    command(['/usr/sbin/runuser', '-u', 'onpf', '--', '/usr/bin/env',
             *[f'{key}={value}' for key, value in environment(config).items()],
             str(CODE / 'current/.venv/bin/python'), '-c',
             'import os; from onpf.production import load_config; load_config(os.environ)'])
    command(['/usr/bin/systemctl', 'enable', '--now', 'onpf.service', 'onpf-backup.timer', 'onpf-monitor.timer'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    storage = sub.add_parser('prepare-storage', help='DESTRUCTIVE: format only positively identified blank EBS disk')
    storage.add_argument('--volume-id', required=True); storage.add_argument('--device', required=True)
    release = sub.add_parser('install-release', help='Install trusted bundle while application and backup are stopped')
    release.add_argument('--bundle', type=Path, required=True); release.add_argument('--sha256', required=True)
    for name in ('configure', 'initialize-db', 'start'):
        action = sub.add_parser(name); action.add_argument('--config', type=Path, required=True)
        if name == 'configure': action.add_argument('--https', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Installation requires an explicitly administered Ubuntu host and root.')
    os.umask(0o077)
    try:
        if args.action == 'prepare-storage': prepare_storage(args.volume_id, args.device)
        elif args.action == 'install-release': install_release(args.bundle, args.sha256)
        else:
            config = validate_config(json.loads(args.config.read_text()))
            if any(config[field] != str(path) for field, path in {
                    'data_root': DATA, 'instance': DATA / 'instance',
                    'database': DATA / 'instance/onpf.sqlite3', 'staging': DATA / 'backups'}.items()):
                raise ValueError('Installed units require the standard /var/lib/onpf paths.')
            if args.action == 'configure': configure(config, args.https)
            elif args.action == 'initialize-db': initialize_database(config)
            else: start(config)
    except (ValueError, OSError, subprocess.CalledProcessError):
        print('ONPF installation step failed; inspect the selected inputs and host state before retrying.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Explicit private Ubuntu gateway setup. No side effects on import/validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
LOCK = HERE / 'gateway-requirements-linux-py312.lock'
METADATA = HERE / 'gateway-lock-metadata.json'
RUNTIME = Path('/opt/onpf-ai-gateway')
ETC = Path('/etc/onpf-ai-gateway')
UNIT = Path('/etc/systemd/system/onpf-ai-gateway.service')
APP_ENV = Path('/etc/onpf/ai-drafting.env')
APP_KEY = Path('/etc/onpf/ai-gateway-key')
USER = 'onpf-ai-gateway'
FIXED = {'gateway_version': '1.103.2', 'bind_host': '127.0.0.1', 'port': 4000,
    'runtime_root': '/opt/onpf-ai-gateway', 'config_root': '/etc/onpf-ai-gateway', 'python': '/usr/bin/python3.12',
    'master_key_file': '/etc/onpf-ai-gateway-keys/master-key',
    'provider_key_file': '/etc/onpf-ai-gateway-keys/provider-key'}
# These snapshots accept ONPF's max_tokens and JSON mode. Other models require
# a separately reviewed wire contract; installer validation must not guess.
MODELS = {'openai/gpt-4o-mini-2024-07-18', 'openai/gpt-4o-2024-08-06'}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(FIXED) | {'model_id', 'lock_sha256'}:
        raise ValueError('invalid_config')
    if any(type(config[name]) is not type(value) or config[name] != value
           for name, value in FIXED.items()):
        raise ValueError('invalid_config')
    if not isinstance(config['model_id'], str) or config['model_id'] not in MODELS:
        raise ValueError('unsupported_model')
    metadata = json.loads(METADATA.read_text(encoding='utf-8'))
    digest = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    if (config['lock_sha256'] != digest or metadata['lock_sha256'] != digest
            or metadata['gateway_version'] != FIXED['gateway_version']
            or metadata['target'] != 'x86_64-unknown-linux-gnu'):
        raise ValueError('invalid_lock')
    return dict(config)


def safe_path(value):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts or path.resolve() != path
            or any(parent.is_symlink() for parent in (path, *path.parents))):
        raise ValueError('unsafe_path')
    return path


def new_target(value):
    path = safe_path(value)
    if path.exists():
        raise ValueError('existing_target')
    return path


def secure_parent(value):
    path = safe_path(value)
    for parent in (path, *path.parents):
        info = parent.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('unsafe_parent')


def read_secret(value, owner):
    try:
        path = safe_path(value)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                    or (os.name != 'nt' and (info.st_mode & 0o077
                        or (owner is not None and info.st_uid != owner)))):
                raise ValueError()
            raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError()
            value = raw.decode('ascii').strip()
        if not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,4000}', value):
            raise ValueError()
        return value
    except Exception:
        raise ValueError('invalid_credential') from None


def gateway_yaml(config):
    return (HERE / 'templates/ai-gateway.yaml.example').read_text(encoding='utf-8').replace(
        'openai/gpt-4o-mini-2024-07-18', config['model_id'])


def application_environment():
    return ('ONPF_AI_DRAFTING_ENABLED=true\nONPF_AI_GATEWAY_URL=http://127.0.0.1:4000/v1\n'
            'ONPF_AI_GATEWAY_KEY_FILE=/etc/onpf/ai-gateway-key\n'
            'ONPF_AI_MODEL=onpf-drafting\nONPF_AI_TIMEOUT_SECONDS=45\n'
            'ONPF_AI_MAX_OUTPUT_TOKENS=4096\nONPF_AI_MAX_EVIDENCE_BYTES=96000\n')


def command(args, **kwargs):
    # pip/provider/package exceptions can contain sensitive text. Never relay
    # child streams, including on failure, and don't inherit tracing/proxy env.
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'HOME': '/nonexistent', 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=environment, **kwargs)
    if result.returncode:
        raise ValueError('gateway_command_failed')


def linux_host():
    return sys.platform == 'linux'


def preflight(config):
    if not linux_host() or os.geteuid() != 0 or platform.machine() != 'x86_64':
        raise ValueError('unsupported_host')
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '24.04':
        raise ValueError('unsupported_host')
    if platform.libc_ver()[0] != 'glibc' or tuple(map(int, platform.libc_ver()[1].split('.'))) < (2, 39):
        raise ValueError('unsupported_host')
    for path in (RUNTIME, ETC, UNIT, APP_ENV, APP_KEY):
        new_target(path)
        secure_parent(path.parent)
    for directory in ('/etc/systemd/system', '/run/systemd/system', '/usr/lib/systemd/system'):
        for name in ('onpf-ai-gateway.service', 'onpf-ai-gateway.service.d'):
            if Path(directory) / name != UNIT:
                new_target(Path(directory) / name)
    interpreter = safe_path(config['python'])
    secure_parent(interpreter.parent)
    info = interpreter.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('unsafe_interpreter')
    command([str(interpreter), '-c', 'import sys; assert sys.version_info[:2] == (3, 12)'])
    for field in ('master_key_file', 'provider_key_file'):
        secure_parent(Path(config[field]).parent)
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 4000))
        except OSError:
            raise ValueError('port_in_use') from None


def account():
    import grp
    import pwd
    try:
        entry = pwd.getpwnam(USER)
    except KeyError:
        return None
    # Never reuse a login account or an identity sharing the application's group.
    app = pwd.getpwnam('onpf')
    if (entry.pw_uid < 100 or entry.pw_uid >= 1000 or entry.pw_gid == app.pw_gid
            or grp.getgrgid(entry.pw_gid).gr_name != USER
            or any(USER in group.gr_mem and group.gr_name != USER for group in grp.getgrall())
            or entry.pw_dir != '/nonexistent' or entry.pw_shell != '/usr/sbin/nologin'):
        raise ValueError('unsafe_account')
    return entry


def write_new(path, data, mode=0o600, user='root', group='root'):
    new_target(path)
    # Creation is exclusive; no existing operator file gets overwritten.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(data)
    shutil.chown(path, user=user, group=group)
    path.chmod(mode)


def install(config):
    config = validate_config(config)
    preflight(config)
    import pwd
    pwd.getpwnam('onpf')  # Existing app account required; never initialize ONPF.
    existing = account()
    master = read_secret(config['master_key_file'], 0)
    provider = read_secret(config['provider_key_file'], 0)
    if master == provider:
        raise ValueError('separate_credentials_required')
    if existing is None:
        command(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir',
                 '/nonexistent', '--shell', '/usr/sbin/nologin', USER])
    # Dedicated writable build directory; the completed runtime becomes root-owned.
    RUNTIME.mkdir(mode=0o750)
    shutil.chown(RUNTIME, user=USER, group=USER)
    locked = RUNTIME / 'requirements.lock'
    write_new(locked, LOCK.read_text(encoding='utf-8'), 0o444)
    python = str(RUNTIME / 'venv/bin/python')
    command(['/usr/sbin/runuser', '-u', USER, '--', config['python'], '-m', 'venv', str(RUNTIME / 'venv')])
    command(['/usr/sbin/runuser', '-u', USER, '--', python, '-m', 'pip', 'install',
             '--disable-pip-version-check', '--no-cache-dir', '--no-compile', '--require-hashes',
             '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '-r', str(locked)])
    command(['/usr/sbin/runuser', '-u', USER, '--', python, '-m', 'pip', 'check'])
    command(['/usr/sbin/runuser', '-u', USER, '--', python, '-c',
             "import importlib.metadata as m; assert m.version('litellm') == '1.103.2'"])
    write_new(RUNTIME / 'launch_ai_gateway.py', (HERE / 'launch_ai_gateway.py').read_text(encoding='utf-8'), 0o444)
    write_new(RUNTIME / 'gateway_asgi.py', (HERE / 'gateway_asgi.py').read_text(encoding='utf-8'), 0o444)
    write_new(RUNTIME / 'gateway-lock-metadata.json', METADATA.read_text(encoding='utf-8'), 0o444)
    # venv interpreter symlinks are expected; do not follow them while securing files.
    for path in [RUNTIME, *RUNTIME.rglob('*')]:
        if path.is_symlink():
            os.lchown(path, 0, 0)
            continue
        shutil.chown(path, user='root', group='root')
        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    ETC.mkdir(mode=0o750)
    shutil.chown(ETC, user='root', group=USER)
    ETC.chmod(0o750)
    write_new(ETC / 'config.yaml', gateway_yaml(config), 0o640, group=USER)
    write_new(ETC / 'gateway.env', f'LITELLM_MASTER_KEY={master}\nOPENAI_API_KEY={provider}\n')
    write_new(ETC / 'operator.json', json.dumps(config, indent=2) + '\n')
    write_new(APP_KEY, master + '\n', 0o400, user='onpf', group='onpf')
    write_new(APP_ENV, application_environment())
    write_new(UNIT, (HERE / 'templates/onpf-ai-gateway.service').read_text(encoding='utf-8'), 0o644)
    command(['/usr/bin/systemctl', 'daemon-reload'])
    # Starting/enabling the gateway and restarting the app are explicit operator
    # steps after review. A failed install leaves files for manual inspection.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'install'))
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.config.stat().st_size > 16384:
            raise ValueError('invalid_config')
        config = validate_config(json.loads(args.config.read_text(encoding='utf-8')))
        if args.action == 'install':
            install(config)
        print('gateway_config_valid' if args.action == 'validate' else 'gateway_installed_requires_start')
        return 0
    except Exception:
        print('gateway_setup_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""Launch only the fixed private gateway; never inherit optional integrations."""
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

CONFIG = '/etc/onpf-ai-gateway/config.yaml'


def child_environment(environment):
    result = {'LITELLM_MASTER_KEY': environment.get('LITELLM_MASTER_KEY', '')}
    if environment.get('OPENAI_API_KEY'):
        result['OPENAI_API_KEY'] = environment['OPENAI_API_KEY']
    if any(not re.fullmatch(r'sk-[A-Za-z0-9_-]{20,4000}', value)
           for value in result.values()):
        raise ValueError('invalid_credential')
    result.update(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C.UTF-8',
        PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', LITELLM_MODE='PRODUCTION', LITELLM_LOG='CRITICAL',
        LITELLM_LOCAL_MODEL_COST_MAP='True', LITELLM_DONT_SHOW_FEEDBACK_BOX='true',
        WORKER_CONFIG=json.dumps({'config': CONFIG, 'telemetry': False, 'debug': False,
            'detailed_debug': False, 'drop_params': False, 'add_function_to_prompt': False,
            'request_timeout': 30}))
    return result


def child_command(python):
    return [python, '-m', 'uvicorn', 'gateway_asgi:app',
            '--host', '127.0.0.1', '--port', '4000', '--workers', '1',
            '--no-access-log', '--log-level', 'critical']


def main():
    try:
        environment = child_environment(os.environ)
        path = Path(CONFIG)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.resolve() != path
                or info.st_uid != 0 or info.st_mode & 0o022
                or importlib.metadata.version('litellm') != '1.103.2'):
            raise ValueError('invalid_runtime')
        if info.st_size > 32768:
            raise ValueError('invalid_config')
        # Future local Ollama routes may omit this provider variable. The
        # initial OpenAI config must have its provider credential before launch.
        if ('os.environ/OPENAI_API_KEY' in path.read_text(encoding='utf-8')
                and 'OPENAI_API_KEY' not in environment):
            raise ValueError('invalid_credential')
        # Fixed Uvicorn bind is authoritative even if another process wins this race.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 4000))
        # Contain prints/tracebacks from every library as well as Python logging.
        # systemd independently redirects both streams, including launcher failures.
        with open(os.devnull, 'wb', buffering=0) as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        os.execve(sys.executable, child_command(sys.executable), environment)
    except Exception:
        print('gateway_start_failed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

"""Installation-owned gateway settings; validation never reads credentials."""
import ipaddress
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

DEFAULTS = {
    'AI_DRAFTING_ENABLED': False,
    'AI_GATEWAY_URL': 'http://127.0.0.1:4000/v1',
    'AI_GATEWAY_KEY_FILE': '',
    'AI_MODEL': 'onpf-drafting',
    'AI_TIMEOUT_SECONDS': 45,
    'AI_MAX_OUTPUT_TOKENS': 4096,
    'AI_MAX_EVIDENCE_BYTES': 96000,
}


def drafting_environment(environment: Mapping[str, str]) -> dict:
    """Map only supported ONPF_AI_* installation settings."""
    if any(environment.get(name) for name in ('ONPF_AI_BASE_URL', 'ONPF_AI_API_KEY', 'ONPF_AI_API_KEY_FILE')):
        raise ValueError('Legacy provider settings require the separate gateway and protected credential file.')
    return {name: environment['ONPF_' + name] for name in DEFAULTS
            if 'ONPF_' + name in environment}


def _enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() in {'true', '1', 'on'}:
            return True
        if value.lower() in {'false', '0', 'off'}:
            return False
    raise ValueError('AI_DRAFTING_ENABLED must be true or false.')


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not (isinstance(value, int) or
            isinstance(value, str) and re.fullmatch(r'[0-9]+', value)):
        raise ValueError(f'{name} must be from {minimum} to {maximum}.')
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f'{name} must be from {minimum} to {maximum}.') from None
    if not minimum <= number <= maximum:
        raise ValueError(f'{name} must be from {minimum} to {maximum}.')
    return number


def _gateway_url(value) -> str:
    error = 'AI_GATEWAY_URL must be HTTPS or literal loopback HTTP, without credentials, query or fragment.'
    # Reject before urlsplit can silently strip control characters. Restrict the
    # base path to ordinary unescaped segments, avoiding ambiguous URL rewrites.
    if not isinstance(value, str) or len(value) > 2048 or any(
            ord(char) <= 32 or ord(char) >= 127 or char in '\\%?#' for char in value):
        raise ValueError(error)
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
        if parts.scheme not in {'https', 'http'} or not host or parts.username is not None or parts.password is not None:
            raise ValueError(error)
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(error)
        if not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*/?', parts.path):
            raise ValueError(error)
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                       for label in host.split('.')):
                raise ValueError(error)
        if parts.scheme == 'http' and (address is None or not address.is_loopback):
            raise ValueError(error)
    except ValueError:
        raise ValueError(error) from None
    return value.rstrip('/')


def validate_settings(config: Mapping) -> dict:
    """Normalize supported settings, fail closed when enabled, and do no I/O."""
    if any(config.get(name) for name in ('AI_BASE_URL', 'AI_API_KEY', 'AI_API_KEY_FILE')):
        raise ValueError('Legacy provider settings require the separate gateway and protected credential file.')
    settings = {name: config.get(name, value) for name, value in DEFAULTS.items()}
    settings['AI_DRAFTING_ENABLED'] = _enabled(settings['AI_DRAFTING_ENABLED'])
    if not settings['AI_DRAFTING_ENABLED']:
        return settings
    settings['AI_GATEWAY_URL'] = _gateway_url(settings['AI_GATEWAY_URL'])
    if not isinstance(settings['AI_MODEL'], str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', settings['AI_MODEL']):
        raise ValueError('AI_MODEL must be one model alias.')
    key_path = settings['AI_GATEWAY_KEY_FILE']
    if not isinstance(key_path, str) or '\x00' in key_path or not key_path or not Path(key_path).is_absolute():
        raise ValueError('AI_GATEWAY_KEY_FILE must be an absolute credential file path.')
    for name, low, high in (('AI_TIMEOUT_SECONDS', 1, 60),
                            ('AI_MAX_OUTPUT_TOKENS', 256, 8192),
                            ('AI_MAX_EVIDENCE_BYTES', 1024, 96000)):
        settings[name] = _integer(settings[name], name, low, high)
    return settings

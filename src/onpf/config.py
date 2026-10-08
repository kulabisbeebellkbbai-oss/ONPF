"""Installation configuration and persistent local signing secret."""
import os
import secrets
import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from onpf.drafting.config import DEFAULTS as DRAFTING_DEFAULTS

DEFAULTS = {
    **DRAFTING_DEFAULTS,
    "DEBUG": False,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "SESSION_COOKIE_SECURE": False,
    "AUTH_COOKIE_NAME": "onpf_session",
    "AUTH_SESSION_SECONDS": 12 * 60 * 60,
    "LOGIN_MAX_ATTEMPTS": 5,
    "LOGIN_THROTTLE_SECONDS": 15 * 60,
    "MAX_CONTENT_LENGTH": 2 * 1024 * 1024,
    # The recipient is installation configuration and is never rendered in public pages.
    "CONTACT_TO": "onpf@proton.me",
    "CONTACT_SMTP_HOST": "",
    "CONTACT_SMTP_PORT": 587,
    "CONTACT_SMTP_FROM": "",
    "CONTACT_SMTP_USER": "",
    "CONTACT_SMTP_PASSWORD": "",
    "CONTACT_SMTP_PASSWORD_FILE": "",
}


def contact_environment(environment):
    """Installation-owned contact settings, shared by local and production startup."""
    names = {
        "ONPF_CONTACT_TO": "CONTACT_TO",
        "ONPF_SMTP_HOST": "CONTACT_SMTP_HOST",
        "ONPF_SMTP_PORT": "CONTACT_SMTP_PORT",
        "ONPF_SMTP_FROM": "CONTACT_SMTP_FROM",
        "ONPF_SMTP_USER": "CONTACT_SMTP_USER",
        "ONPF_SMTP_PASSWORD": "CONTACT_SMTP_PASSWORD",
        "ONPF_SMTP_PASSWORD_FILE": "CONTACT_SMTP_PASSWORD_FILE",
    }
    return {config_name: environment[env_name] for env_name, config_name in names.items()
            if env_name in environment}


def persistent_identity(instance: Path) -> str:
    """Nonsecret readiness identity, distinct from signing/authentication secrets."""
    path = instance / '.instance-id'
    try:
        with path.open('x', encoding='utf-8') as handle:
            value = str(uuid4())
            handle.write(value)
    except FileExistsError:
        value = path.read_text(encoding='utf-8').strip()
    return str(UUID(value))


def readiness_identity(instance: Path, database: Path) -> str:
    path = str(database.resolve()).replace('\\', '/')
    if os.name == 'nt':
        path = path.lower()
    return hashlib.sha256((persistent_identity(instance) + '\n' + path).encode('utf-8')).hexdigest()


def persistent_secret(instance: Path) -> str:
    instance.mkdir(parents=True, exist_ok=True)
    path = instance / ".secret"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            raise RuntimeError("The instance signing secret is invalid.")
        return value
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        value = secrets.token_hex(32)
        file.write(value)
    return value

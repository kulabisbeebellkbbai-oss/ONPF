"""Bounded OpenAI-compatible HTTP transport with no domain or persistence access."""
import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import time
from pathlib import Path
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread, Timer
from urllib.parse import urlsplit

from onpf.drafting.config import validate_settings

REQUEST_BYTE_CAP = 131072
RESPONSE_BYTE_CAP = 131072
CREDENTIAL_BYTE_CAP = 4096
# A stalled OS resolver cannot be killed safely. Bound its background footprint
# per ONPF process and fail immediately when all four slots remain occupied.
_RESOLVER_SLOTS = BoundedSemaphore(4)


class GatewayError(RuntimeError):
    """A safe diagnostic that contains no upstream text, URLs or credentials."""


def _credential(filename: str) -> str:
    try:
        path = Path(filename)
        if path.is_symlink():
            raise ValueError()
        flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0))
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > CREDENTIAL_BYTE_CAP:
                raise ValueError()
            if os.name != 'nt' and info.st_mode & 0o077:
                raise ValueError()
            raw = handle.read(CREDENTIAL_BYTE_CAP + 1)
        if len(raw) > CREDENTIAL_BYTE_CAP:
            raise ValueError()
        key = raw.decode('ascii').strip()
        if not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', key):
            raise ValueError()
        return key
    except (OSError, ValueError, UnicodeError):
        raise GatewayError('The drafting gateway credential is unavailable or invalid.') from None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    return remaining


def _addresses(host: str, port: int, deadline: float) -> list:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        address = (host, port, 0, 0) if literal.version == 6 else (host, port)
        return [(family, socket.SOCK_STREAM, 0, '', address)]
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        raise TimeoutError()
    results = Queue(maxsize=1)

    def resolve():
        try:
            results.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
        except Exception:
            # The worker returns no exception values, which may include private
            # resolver details. It only resolves; it never opens a connection.
            results.put((False, None))
        finally:
            _RESOLVER_SLOTS.release()

    worker = Thread(target=resolve, daemon=True, name='onpf-gateway-resolver')
    try:
        worker.start()
    except RuntimeError:
        _RESOLVER_SLOTS.release()
        raise OSError() from None
    try:
        success, addresses = results.get(timeout=_remaining(deadline))
    except Empty:
        raise TimeoutError() from None
    _remaining(deadline)
    if not success or not addresses:
        raise OSError()
    return addresses


class Completion(str):
    """Retain validated token usage without changing the text contract."""
    def __new__(cls,content,usage=None):
        value=super().__new__(cls,content)
        value.usage=usage
        return value


def _response_content(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode('utf-8'))
        choices = value['choices']
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        choice = choices[0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError()
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        return Completion(content,value.get('usage'))
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, RecursionError):
        raise GatewayError('The drafting gateway returned an invalid response.') from None


def complete(settings: dict, messages: list[dict], response_format: dict | None = None) -> str:
    """Return one unsaved suggestion; callers validate its domain JSON contract."""
    try:
        settings = validate_settings(settings)
    except ValueError:
        raise GatewayError('The drafting gateway configuration is invalid.') from None
    if not settings['AI_DRAFTING_ENABLED']:
        raise GatewayError('AI drafting is disabled.')
    try:
        body = json.dumps({'model': settings['AI_MODEL'], 'messages': messages,
                           'max_tokens': settings['AI_MAX_OUTPUT_TOKENS'], 'stream': False,
                           'response_format': response_format if response_format is not None else {'type': 'json_object'}},
                          ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise GatewayError('The drafting gateway request is invalid.') from None
    if len(body) > REQUEST_BYTE_CAP:
        raise GatewayError('The drafting gateway request exceeds its byte limit.')
    key = _credential(settings['AI_GATEWAY_KEY_FILE'])
    endpoint = urlsplit(settings['AI_GATEWAY_URL'])
    connection_type = http.client.HTTPSConnection if endpoint.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(endpoint.hostname, endpoint.port, timeout=settings['AI_TIMEOUT_SECONDS'])
    deadline = time.monotonic() + settings['AI_TIMEOUT_SECONDS']
    timer = None
    transport = None
    response = None

    def expire():
        # Interrupt header reads too: a socket timeout alone resets whenever an
        # upstream sends a byte and cannot bound a slowly trickling response.
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def connect(address, _timeout, source_address=None):
        nonlocal transport
        addresses = _addresses(address[0], address[1], deadline)
        for family, kind, protocol, _, target in addresses:
            _remaining(deadline)
            transport = socket.socket(family, kind, protocol)
            try:
                transport.settimeout(_remaining(deadline))
                if source_address is not None:
                    transport.bind(source_address)
                transport.connect(target)
                _remaining(deadline)
                return transport
            except OSError:
                transport.close()
        raise OSError()

    try:
        # http.client neither follows redirects nor reads environment proxies.
        # HTTPSConnection uses the standard verified TLS context.
        # Use http.client's socket creation hook to bound DNS and each resolved
        # address attempt. HTTPS still wraps this socket with verified TLS and
        # the original hostname for certificate validation/SNI.
        connection._create_connection = connect
        timer = Timer(_remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        connection.connect()
        transport = connection.sock
        connection.sock.settimeout(_remaining(deadline))
        connection.request('POST', endpoint.path + '/chat/completions', body,
                           headers={'Authorization': 'Bearer ' + key,
                                    'Content-Type': 'application/json', 'Accept': 'application/json'})
        connection.sock.settimeout(_remaining(deadline))
        response = connection.getresponse()
        if response.status != 200:
            raise GatewayError('The drafting gateway is unavailable.')
        length = response.getheader('Content-Length')
        if length is not None and (not re.fullmatch(r'[0-9]+', length) or int(length) > RESPONSE_BYTE_CAP):
            raise GatewayError('The drafting gateway response exceeds its byte limit or is invalid.')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise GatewayError('The drafting gateway returned an invalid response.')
        chunks, size = [], 0
        while True:
            # The response retains its socket even when Connection: close makes
            # http.client detach connection.sock after parsing headers.
            _remaining(deadline)
            chunk = response.read1(min(8192, RESPONSE_BYTE_CAP + 1 - size))
            _remaining(deadline)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > RESPONSE_BYTE_CAP:
                raise GatewayError('The drafting gateway response exceeds its byte limit.')
        if length is not None and size != int(length):
            raise GatewayError('The drafting gateway returned an invalid response.')
        return _response_content(b''.join(chunks))
    except (OSError, http.client.HTTPException, ValueError, OverflowError):
        raise GatewayError('The drafting gateway is unavailable.') from None
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        connection.close()
        if transport is not None:
            transport.close()

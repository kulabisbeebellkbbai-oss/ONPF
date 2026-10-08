"""Shared Waitress request limits for local and production entry points."""

MAX_REQUEST_BYTES = 26 * 1024 * 1024
RECEIVE_BYTES = 8192
REQUEST_BODY_OPTIONS = {
    'max_request_body_size': MAX_REQUEST_BYTES,
    'recv_bytes': RECEIVE_BYTES,
    # Waitress checks chunked-body size after appending one socket receive.
    # Keep that final receive in memory too, before Flask's tighter route limits.
    'inbuf_overflow': MAX_REQUEST_BYTES + RECEIVE_BYTES,
}

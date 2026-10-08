"""Expose only ONPF completions while retaining the pinned proxy's lifespan."""
from litellm.proxy.proxy_server import app as _proxy


async def app(scope, receive, send):
    if scope['type'] == 'lifespan' or (scope['type'] == 'http'
            and scope.get('method') == 'POST'
            and scope.get('path') == '/v1/chat/completions'):
        # LiteLLM keeps its config/startup/shutdown and bearer authentication.
        await _proxy(scope, receive, send)
    elif scope['type'] == 'http':
        # The pinned app mounts UI/_next assets unconditionally. An outer exact
        # allowlist also hides admin APIs and future mounts without route surgery.
        await send({'type': 'http.response.start', 'status': 404,
                    'headers': [(b'content-type', b'application/json'),
                                (b'content-length', b'21')]})
        await send({'type': 'http.response.body', 'body': b'{"error":"not_found"}'})
    elif scope['type'] == 'websocket':
        await send({'type': 'websocket.close', 'code': 1008})

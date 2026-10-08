"""Installed question schema metadata must count toward scoped request limits."""
import json

import pytest

from onpf.drafting import evidence, usage
from onpf.drafting.contracts import build_messages, output_format
from onpf.errors import DomainError
from test_ai_drafting import drafting, server, source, suggestion


def test_strict_schema_bytes_are_reserved_before_provider_contact(drafting, app, tmp_path, owner, program):
    target = {'kind': 'questions'}
    handle = source(owner, program)
    selected = evidence.select(owner, program['id'], target, [handle])
    messages = build_messages(target, selected, '', {})
    body = {'model': app.config['AI_MODEL'], 'messages': messages,
            'max_tokens': app.config['AI_MAX_OUTPUT_TOKENS'], 'stream': False,
            'response_format': output_format(target)}
    size = len(json.dumps(body, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf8'))
    assert size > len(json.dumps(messages, ensure_ascii=False).encode('utf8')) + 1024
    usage.set_limits(owner, 'system', '', {'request_bytes': size - 1})
    with server(app, tmp_path, json.dumps(suggestion('questions', handle))) as calls:
        with pytest.raises(DomainError) as refused:
            drafting.generate(owner, program['id'], target, [handle], '', {})
    assert refused.value.code == 'ai_resource_limit'
    assert calls == []

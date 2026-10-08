"""Authorized readable references shared by ordinary workflow templates."""
from flask import g
from onpf.auth.service import get_principal
from onpf.errors import DomainError
from onpf.db import get_db


def record_label(kind, key):
    cache = g.setdefault('readable_records', {})
    identity = (kind, key)
    if identity in cache:
        return cache[identity]
    tables = {'response': 'responses', 'proposal': 'proposals', 'decision': 'decisions',
              'question': 'inquiry_questions', 'issued_question': 'batch_questions',
              'material': 'materials', 'batch': 'batches'}
    table = tables.get(kind)
    if not table:
        return kind.replace('_', ' ').title()
    try:
        row = get_db().execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
        if not row:
            return 'Unavailable ' + kind.replace('_', ' ')
        row = dict(row)
        program_id = row.get('program_id')
        if not program_id and row.get('batch_id'):
            parent = get_db().execute('SELECT program_id FROM batches WHERE id=?', (row['batch_id'],)).fetchone()
            program_id = parent['program_id'] if parent else None
        if program_id:
            from onpf.auth.service import require_role
            require_role(get_principal(), program_id, {'owner','facilitator','viewer','contributor'})
        if kind == 'response':
            from onpf.refinement.service import coverage
            for response in coverage(get_principal(), program_id):
                cache[('response', response['id'])] = response['reference'] + ' — ' + response['question_text'][:80]
        else:
            caption = row.get('title') or row.get('text') or row.get('outcome') or kind.replace('_',' ').title()
            if kind == 'question':
                import json
                caption = json.loads(row['content']).get('text','Question')
            number = get_db().execute(f'SELECT rowid FROM {table} WHERE id=?', (key,)).fetchone()[0]
            cache[identity] = f'{kind.replace("_"," ").title()} {number} — {caption[:100]}'
    except DomainError:
        return 'Unavailable ' + kind.replace('_', ' ')
    return cache.get(identity, kind.replace('_',' ').title())


def source_label(source):
    kind, key = source.get('kind'), source.get('record_key')
    if kind in {'question','issued_question'} and source.get('content'):
        return 'Question — ' + source['content'].get('text','')[:100]
    if kind in {'supporting','organizer_clarification'} and source.get('content'):
        content=source['content']
        return source['label'] + ' — ' + (content.get('title') or content.get('text',''))[:100]
    if kind == 'document':
        from onpf.programs.framework import load_framework
        return load_framework()['documents'][key]['title']
    if kind in {'response','proposal','decision','question','issued_question','material','batch'}:
        return record_label(kind, key)
    return source.get('label', 'Selected source')

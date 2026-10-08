"""Embedded review acknowledgments survive transformations as document content."""
from onpf.errors import DomainError

GUARD = 'REVIEW REQUIRED — Read and check this entire section. Manually delete this statement after reviewing the text. This section is an unreviewed draft until that review is completed.'


def outstanding(value):
    if isinstance(value,str):
        return 'REVIEW REQUIRED' in value
    if isinstance(value,dict):
        return any(outstanding(child) for child in value.values())
    if isinstance(value,list):
        return any(outstanding(child) for child in value)
    return False


def protect_sections(fields, current, *, initial=False):
    result = dict(fields)
    sections = dict(result.get('sections', {}))
    old = current.get('sections', {})
    for key, text in sections.items():
        previous = old.get(key,'')
        if initial and previous.strip():
            sections[key] = previous
        elif outstanding(previous) and not text.strip():
            sections[key] = previous
        elif text.strip() and (not previous.strip() or outstanding(previous)) and not outstanding(text):
            sections[key] = GUARD + '\n\n' + text
    result['sections'] = sections
    return result


def require_reviewed(documents):
    affected = [key for key,value in documents.items() if outstanding(value)]
    if affected:
        raise DomainError('unreviewed_sections','Read and manually remove the section review statements before verification: ' + ', '.join(affected),422)

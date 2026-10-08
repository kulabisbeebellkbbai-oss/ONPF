import re


def test_all_current_workflow_sources_have_clean_punctuation():
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'src/onpf'
    damaged=('\u00e2\u20ac\u201d','\u00e2\u20ac\u201c','\u00c2\u00b7','\u00c3\u201a')
    findings=[]
    for path in source.rglob('*'):
        if path.suffix in {'.py','.html','.js','.css'}:
            text=path.read_text(encoding='utf8')
            if any(token in text for token in damaged):
                findings.append(str(path.relative_to(source)))
    assert not findings

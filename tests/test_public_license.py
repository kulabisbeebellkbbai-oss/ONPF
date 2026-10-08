"""The public license and exported package must agree, including installed builds."""
from html import unescape
from pathlib import Path
from zipfile import ZipFile


def test_public_license_is_linked_and_readable_without_login(client):
    page = client.get('/login')
    assert 'href="/license"' in page.text
    license_page = client.get('/license')
    assert license_page.status_code == 200
    text = unescape(license_page.text)
    assert 'MIT No Attribution' in text
    assert 'Copyright (c) 2026 Christopher Kula' in text
    assert 'The above copyright notice' not in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_exported_license_matches_public_and_repository_copy(client, owner, program, tmp_path):
    from onpf.releases.service import prepare_candidate, approve_candidate
    from onpf.exports.package import build_package
    candidate = prepare_candidate(owner, program['id'], program['revision'])
    release = approve_candidate(owner, candidate['id'])
    with ZipFile(build_package(owner, release['release_id'], tmp_path)) as archive:
        license_text = archive.read('LICENSE').decode()
    public = client.get('/static/LICENSE.txt')
    assert public.status_code == 200
    assert license_text.splitlines() == public.text.splitlines()
    assert license_text == (Path(__file__).parents[1] / 'LICENSE').read_text(encoding='utf-8')
    assert 'MIT No Attribution' in license_text
    assert 'Christopher Kula' in license_text
    assert 'subject to the following conditions' not in license_text

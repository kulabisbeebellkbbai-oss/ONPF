"""Build committed source only; extract a trusted-checksum bundle without tar links."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

MAX_BYTES = 128 * 1024 * 1024
ROOT_FILES = {'LICENSE', 'README.md', 'pyproject.toml', 'requirements.lock',
              'THIRD_PARTY_NOTICES.md', 'CONTRIBUTING.md'}
PUBLIC_EXAMPLES = {'examples/fictional-group-input.json'}


def allowed(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '\\' in name or ':' in name
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        return False
    if any(part.startswith('.') or part in {'instance', '__pycache__'} for part in path.parts):
        return False
    # Checked-in examples are public; live operator environments/configuration
    # and gateway credentials must never enter a release, even if committed.
    if (path.name.endswith('.env') or path.name in
            {'master-key', 'provider-key', 'ai-gateway-key', 'operator.json', 'gateway.env'}):
        return False
    if re.search(r'\.(?:sqlite3?|db)(?:-|$)|\.(?:pem|key|private\.json)$', name, re.I):
        return False
    # Top-level private archives/exports are outside this allowlist. Those names
    # are also legitimate application packages and must not be excluded in src.
    return (name in ROOT_FILES or name in PUBLIC_EXAMPLES
            or name.startswith(('src/onpf/', 'deploy/aws/', 'docs/')))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def build_bundle(source, output):
    """Package HEAD blobs, ignoring both dirty and untracked working files."""
    source, output = Path(source), Path(output)
    commit = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(['git', '-C', str(source), 'ls-tree', '-r', '-z', commit],
                          check=True, capture_output=True).stdout
    selected = []
    for entry in tree.split(b'\0'):
        if not entry: continue
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode('utf-8')
        if not allowed(name): continue
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('Selected source contains a link or special file.')
        selected.append((name, object_id))
    # cat-file reads original blobs; git archive may apply platform EOL/export
    # attributes, which would make Windows and Linux builds differ.
    raw = subprocess.run(['git', '-C', str(source), 'cat-file', '--batch'],
                         input=b''.join(oid + b'\n' for _, oid in selected),
                         check=True, capture_output=True).stdout
    stream, files = io.BytesIO(raw), {}
    for name, object_id in selected:
        oid, kind, size = stream.readline().split()
        if oid != object_id or kind != b'blob': raise ValueError('Unexpected Git blob response.')
        files[name] = stream.read(int(size))
        if stream.read(1) != b'\n': raise ValueError('Incomplete Git blob response.')
    if not {'LICENSE', 'pyproject.toml', 'requirements.lock'} <= files.keys():
        raise ValueError('Release is missing its license or dependency metadata.')
    manifest = {'format': 1, 'commit': commit,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    files['release.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    if sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('Release exceeds the source bundle size limit.')
    # Exclusive publication: a release artifact must never be silently overwritten.
    with output.open('xb') as stream:
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w') as archive:
                for name, data in sorted(files.items()):
                    member = tarfile.TarInfo(name)
                    member.size, member.mode, member.mtime = len(data), 0o644, 0
                    archive.addfile(member, io.BytesIO(data))
    receipt = {'commit': commit, 'sha256': digest(output), 'file': output.name}
    with Path(str(output) + '.sha256.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return receipt


def extract_bundle(bundle, expected_sha256, destination):
    """Validate every member and manifest before creating a fresh destination.

    SHA-256 authenticates bytes only when expected_sha256 came from a trusted
    operator copy. The adjacent unsigned manifest is not publisher identity.
    """
    destination = Path(destination)
    if (not re.fullmatch('[0-9a-f]{64}', expected_sha256)
            or digest(bundle) != expected_sha256):
        raise ValueError('Bundle checksum does not match the trusted expected checksum.')
    if destination.exists() or destination.is_symlink() or destination.resolve() != destination.absolute():
        raise ValueError('Extraction requires a new destination without redirected parents.')
    files, total = {}, 0
    with tarfile.open(bundle, 'r:*') as archive:
        for member in archive:
            total += member.size
            if (not member.isfile() or member.name in files
                    or (member.name != 'release.json' and not allowed(member.name))
                    or member.size < 0 or total > MAX_BYTES or len(files) >= 10000):
                raise ValueError('Unsafe or oversized bundle member.')
            files[member.name] = archive.extractfile(member).read()
    try:
        manifest = json.loads(files.pop('release.json'))
        if (manifest['format'] != 1 or not re.fullmatch('[0-9a-f]{40}', manifest['commit'])
                or manifest['files'] != {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
                or not {'LICENSE', 'pyproject.toml', 'requirements.lock'} <= files.keys()):
            raise ValueError('Invalid release manifest.')
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('Invalid release manifest.') from None
    # Reject file-as-parent conflicts before extraction as well.
    for name in files:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError('Conflicting bundle member paths.')
    destination.mkdir(mode=0o755)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        path.chmod(0o644)
    (destination / 'release.json').write_text(json.dumps(manifest, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(args.source, args.output), indent=2))


if __name__ == '__main__':
    main()

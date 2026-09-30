"""Fail closed on private paths, data inventories, and unreviewed binary assets.

Use --private-values with an external newline-delimited denylist for a local
release check. Matches are never printed. This complements manual source review.
"""
from __future__ import annotations
import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

ROOTS = ('Users', 'home', 'Volumes', 'mnt', 'sdf', 'sps', 'lustre', 'gpfs', 'private', 'net', 'scratch')
PRIVATE_PATH = re.compile(rb'/(?:' + '|'.join(ROOTS).encode() + rb')/[^\s\x00"\'<>]+', re.I)
REMOTE_PATH = re.compile(rb'(?:[a-z0-9_.-]+@[^\s:]+:|[A-Z]:\\\\)[^\s]+', re.I)
DATA_SUFFIXES = {'.csv', '.tsv', '.parquet', '.sqlite', '.db', '.png', '.jpg', '.fits', '.gz'}


def check(name, data, private_values=()):
    payload = name.encode() + b'\n' + data
    problems = []
    if PRIVATE_PATH.search(payload) or REMOTE_PATH.search(payload):
        problems.append('private path pattern')
    if any(value and value.lower() in payload.lower() for value in private_values):
        problems.append('private value')
    if Path(name).suffix.lower() in DATA_SUFFIXES:
        problems.append('data inventory or binary asset')
    if b'\x00' in data:
        problems.append('unreviewed binary')
    return problems


def audit(root, artifacts=(), history=False, private_values=()):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args])
    entries = []
    names = git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
    for name in sorted(set(names)):
        if name and (root / name.decode()).is_file():
            entries.append((name.decode(), (root / name.decode()).read_bytes()))
    # Include staged content even when the worktree differs.
    for line in git('ls-files', '-s', '-z').split(b'\0'):
        if line:
            meta, name = line.split(b'\t', 1)
            entries.append((name.decode(), git('cat-file', 'blob', meta.split()[1].decode())))
    if history:
        for commit in git('rev-list', '--all').splitlines():
            for line in git('ls-tree', '-rz', commit.decode()).split(b'\0'):
                if line:
                    meta, name = line.split(b'\t', 1)
                    if meta.split()[1] == b'blob':
                        entries.append((name.decode(), git('cat-file', 'blob', meta.split()[2].decode())))
    for artifact in artifacts:
        if zipfile.is_zipfile(artifact):
            with zipfile.ZipFile(artifact) as archive:
                entries.extend((n, archive.read(n)) for n in archive.namelist() if not n.endswith('/'))
        else:
            with tarfile.open(artifact) as archive:
                entries.extend((m.name, archive.extractfile(m).read()) for m in archive if m.isfile())
    failures = sum(bool(check(name, data, private_values)) for name, data in entries)
    print(f'Public audit: {len(entries)} entries checked; {failures} failures (values suppressed).')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--artifact', action='append', type=Path, default=[])
    parser.add_argument('--private-values', type=Path)
    args = parser.parse_args()
    values = args.private_values.read_bytes().splitlines() if args.private_values else ()
    raise SystemExit(bool(audit(Path.cwd(), args.artifact, args.history, values)))

if __name__ == '__main__':
    main()

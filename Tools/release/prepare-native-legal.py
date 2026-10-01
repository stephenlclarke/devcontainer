#!/usr/bin/env python3
# Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
"""Retain exact Git source legal files without restoring or building checkouts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse


MAX_LEGAL_BYTES = 2 * 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def github_repository(location: str) -> str:
    url = urlparse(location)
    repository = url.path.removeprefix('/').removesuffix('.git')
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) is None
            or any(part in {'.', '..'} for part in repository.split('/'))
            or url.path not in {'/' + repository, '/' + repository + '.git'}):
        raise ValueError('Legal source must be an exact public GitHub repository')
    return repository


def api(endpoint: str) -> dict:
    result = subprocess.run(['gh', 'api', endpoint], stdin=subprocess.DEVNULL,
                            capture_output=True, check=True, timeout=30)
    if len(result.stdout) > 4 * MAX_LEGAL_BYTES:
        raise ValueError('GitHub legal response exceeds its bound')
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError('GitHub legal response must be an object')
    return value


def legal_names(tree: dict) -> list[dict]:
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        raise ValueError('Legal source root tree is incomplete')
    entries = tree['tree']
    if any(not isinstance(row, dict) or not isinstance(row.get('path'), str) for row in entries):
        raise ValueError('Legal source root tree has invalid entries')
    if len({row['path'] for row in entries}) != len(entries):
        raise ValueError('Legal source tree repeats paths')
    files = [row for row in entries if row.get('type') == 'blob'
             and row.get('mode') in {'100644', '100755'} and '/' not in row['path']]
    licenses = sorted((row for row in files if row['path'].casefold().startswith('license')),
                      key=lambda row: row['path'].casefold())
    if not licenses:
        licenses = sorted((row for row in files if row['path'].casefold().startswith('copying')),
                          key=lambda row: row['path'].casefold())
    if not licenses:
        raise ValueError('Exact source has no root license')
    notices = sorted((row for row in files if row['path'].casefold().startswith('notice')),
                     key=lambda row: row['path'].casefold())
    return licenses + notices


def read_blob(repository: str, row: dict, fetch=api) -> dict:
    name = row.get('path', '')
    if (not name or '/' in name or name in {'.', '..'}
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise ValueError('Legal filename contains unsafe provenance characters')
    identifier = row.get('sha', '')
    if re.fullmatch(r'[0-9a-f]{40}', identifier) is None:
        raise ValueError('Legal blob lacks a Git identity')
    blob = fetch(f'repos/{repository}/git/blobs/{identifier}')
    if blob.get('sha') != identifier or blob.get('encoding') != 'base64':
        raise ValueError('Legal blob identity or encoding differs')
    data = base64.b64decode(''.join(blob.get('content', '').split()), validate=True)
    if not data or len(data) > MAX_LEGAL_BYTES or blob.get('size') != len(data):
        raise ValueError('Legal blob size differs or is outside its bound')
    header = b'blob ' + str(len(data)).encode('ascii') + b'\0'
    if hashlib.sha1(header + data).hexdigest() != identifier:
        raise ValueError('Legal blob bytes differ from the selected Git object')
    return {'name': row['path'], 'gitBlob': identifier, 'sha256': digest(data),
            'text': data.decode('utf-8')}


def collect(dependencies: list, require_license, fetch=api) -> list[dict]:
    output = []
    for dependency in dependencies:
        repository = github_repository(dependency.location)
        commit = fetch(f'repos/{repository}/git/commits/{dependency.revision}')
        if commit.get('sha') != dependency.revision:
            raise ValueError('Legal source commit differs from the resolved pin')
        tree_id = commit.get('tree', {}).get('sha', '')
        if re.fullmatch(r'[0-9a-f]{40}', tree_id) is None:
            raise ValueError('Legal source commit lacks a root tree')
        tree = fetch(f'repos/{repository}/git/trees/{tree_id}')
        if tree.get('sha') != tree_id:
            raise ValueError('Legal root tree differs from the selected commit')
        files = [read_blob(repository, row, fetch) for row in legal_names(tree)]
        require_license(dependency, files[0]['text'])
        output.append({'identity': dependency.identity, 'revision': dependency.revision,
                       'location': dependency.location, 'version': dependency.version,
                       'license': dependency.license, 'rootTree': tree_id, 'files': files})
    return output


def render(rows: list[dict]) -> str:
    sections = ['devcontainer third-party notices', '================================', '',
                'Legal texts from the exact Git revisions in the selected dependency lock.', '']
    for row in rows:
        sections.extend(['=' * 78, 'Dependency: ' + row['identity'], 'Version: ' + row['version'],
                         'Revision: ' + row['revision'], 'Source: ' + row['location'],
                         'Declared license: ' + row['license'], ''])
        for file in row['files']:
            sections.extend(['----- ' + file['name'] + ' -----',
                             file['text'].replace('\r\n', '\n').rstrip(), ''])
    return '\n'.join(sections).rstrip() + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolved', required=True, type=Path)
    parser.add_argument('--license-manifest', required=True, type=Path)
    parser.add_argument('--release-tools', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    for attribute in ('resolved', 'license_manifest', 'release_tools'):
        path = getattr(args, attribute).absolute()
        if path.resolve(strict=True) != path:
            raise ValueError('Legal policy and source paths must be physical')
        setattr(args, attribute, path)
    if (not args.output.is_absolute() or args.output.exists() or args.output.is_symlink()
            or args.output.parent.resolve() != args.output.parent):
        raise ValueError('Legal output requires a fresh physical directory')
    if (args.output.parent.stat().st_dev != Path.home().stat().st_dev
            or args.output.parent.stat().st_uid != os.getuid()):
        raise ValueError('Completed legal evidence requires user-owned internal storage')
    policies = {name: (args.release_tools / name).read_bytes()
                for name in ('dependency_metadata.py', 'write-third-party-notices.py')}
    if any((args.release_tools / name).resolve(strict=True) != args.release_tools / name
           for name in policies):
        raise ValueError('Legal policy files must not be aliases')
    collector = Path(__file__).read_bytes()
    # Reuse the existing strict ledger and reviewed license signatures.
    import sys
    sys.path.insert(0, str(args.release_tools))
    from dependency_metadata import load_dependencies
    spec = importlib.util.spec_from_file_location('notice_writer',
                                                   args.release_tools / 'write-third-party-notices.py')
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    lock_bytes = args.resolved.read_bytes()
    ledger_bytes = args.license_manifest.read_bytes()
    pins = json.loads(lock_bytes)['pins']
    ledger = json.loads(ledger_bytes)
    selected = {'schemaVersion': 1, 'licenses': {pin['identity']: ledger['licenses'][pin['identity']]
                                              for pin in pins}}
    args.output.mkdir(mode=0o700)
    selected_path = args.output / 'dependency-licenses.json'
    selected_path.write_text(json.dumps(selected, indent=2, sort_keys=True) + '\n')
    dependencies = load_dependencies(args.resolved, selected_path)
    rows = collect(dependencies, writer.require_declared_license)
    if args.resolved.read_bytes() != lock_bytes or args.license_manifest.read_bytes() != ledger_bytes:
        raise ValueError('Legal source lock or reviewed ledger changed during collection')
    if (any((args.release_tools / name).read_bytes() != value for name, value in policies.items())
            or Path(__file__).read_bytes() != collector):
        raise ValueError('Legal collection policy changed during execution')
    notices = render(rows).encode()
    document = {'schemaVersion': 1, 'kind': 'source-pinned-legal-bundle',
                'resolvedSHA256': digest(lock_bytes), 'reviewedLedgerSHA256': digest(ledger_bytes),
                'selectedLedgerSHA256': digest(selected_path.read_bytes()),
                'policySHA256': {name: digest(value) for name, value in policies.items()},
                'collectorSHA256': digest(collector),
                'noticesSHA256': digest(notices), 'dependencies': rows}
    (args.output / 'THIRD-PARTY-NOTICES.txt').write_bytes(notices)
    # The final manifest is written last; interruption leaves unadmitted evidence.
    (args.output / 'legal.json').write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()

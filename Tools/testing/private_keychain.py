"""Disposable default keychain inside an owned case HOME, never the login HOME."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import time


class KeychainAPI:
    """Small Security.framework boundary; passwords never enter arguments/logs."""

    def __init__(self):
        self.security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ref = ctypes.c_void_p
        self.security.SecKeychainCreate.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ref, ctypes.c_ubyte, ref, ctypes.POINTER(ref)]
        self.security.SecKeychainCopyDefault.argtypes = [ctypes.POINTER(ref)]
        self.security.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(ref)]
        self.security.SecKeychainGetPath.argtypes = [ref, ctypes.POINTER(ctypes.c_uint32), ref]
        self.security.SecKeychainGetStatus.argtypes = [ref, ctypes.POINTER(ctypes.c_uint32)]
        self.security.SecKeychainDelete.argtypes = [ref]
        self.core.CFRelease.argtypes = [ref]
        self.check(self.security.SecKeychainSetUserInteractionAllowed(False))

    @staticmethod
    def check(status):
        if status != 0:
            raise RuntimeError(f'Private test keychain operation failed: OSStatus {status}')

    def create(self, path: Path):
        password = secrets.token_hex(32).encode()
        reference = ctypes.c_void_p()
        try:
            self.check(self.security.SecKeychainCreate(
                os.fsencode(path), len(password), password, False, None, ctypes.byref(reference)))
        finally:
            if reference.value:
                self.core.CFRelease(reference)

    def default(self) -> dict:
        reference = ctypes.c_void_p()
        self.check(self.security.SecKeychainCopyDefault(ctypes.byref(reference)))
        try:
            buffer = ctypes.create_string_buffer(4096)
            size, status = ctypes.c_uint32(len(buffer)), ctypes.c_uint32()
            self.check(self.security.SecKeychainGetPath(reference, ctypes.byref(size), buffer))
            self.check(self.security.SecKeychainGetStatus(reference, ctypes.byref(status)))
            return {'path': os.fsdecode(buffer.value), 'unlocked': bool(status.value & 1)}
        finally:
            self.core.CFRelease(reference)

    def delete(self, path: Path):
        reference = ctypes.c_void_p()
        self.check(self.security.SecKeychainOpen(os.fsencode(path), ctypes.byref(reference)))
        try:
            self.check(self.security.SecKeychainDelete(reference))
        finally:
            self.core.CFRelease(reference)


def owned_path(root: Path) -> Path:
    """Require the existing runtime ownership marker before any keychain API."""
    if (not root.is_absolute() or root.resolve() != root or not root.is_dir()
            or Path(os.environ.get('HOME', '')) != root or root == Path(pwd.getpwuid(os.getuid()).pw_dir)):
        raise ValueError('Private keychain requires the isolated owned case HOME')
    info = root.stat()
    marker = root / 'owner.json'
    if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or marker.is_symlink() or json.loads(marker.read_text()).get('root') != str(root)):
        raise ValueError('Private keychain case ownership differs')
    path = root / 'Library/Keychains/login.keychain-db'
    for entry in (root / 'Library', path.parent, path, path.with_name('login.keychain')):
        if entry.is_symlink() or entry.resolve() != entry:
            raise ValueError('Private keychain path is aliased')
    return path


def operate(root: Path, action: str, *, api_factory=KeychainAPI) -> dict:
    path = owned_path(root)
    if action not in {'create', 'delete'}:
        raise ValueError('Unknown private keychain operation')
    if action == 'delete' and not path.exists():
        if path.with_name('login.keychain').exists():
            raise ValueError('Unexpected legacy private keychain needs reconciliation')
        return {'status': 'absent'}
    if action == 'create' and (path.exists() or path.with_name('login.keychain').exists()):
        raise ValueError('Private keychain already exists; reconcile instead of recreating')
    api = api_factory()
    if action == 'create':
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Stock Security uses this conventional name in the isolated HOME.
        # Do not call SetDefault/SetSearchList or edit operator preferences.
        api.create(path.with_name('login.keychain'))
        expected = {'path': str(path), 'unlocked': True}
        if api.default() != expected:
            raise ValueError('New default keychain is not the unlocked case keychain')
        return {'status': 'ready', **expected}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ValueError('Private keychain file ownership differs')
    api.delete(path)
    if path.exists():
        raise ValueError('Private keychain survived removal')
    return {'status': 'deleted'}


def require_keychain_stopped(records: dict[str, bytes]) -> int:
    """Never infer helper completion from PID absence after losing its handle."""
    attempts = []
    for name, payload in records.items():
        match = re.fullmatch(r'keychain-([0-9]{4})-intent.json', name)
        if match:
            attempts.append(int(match[1]))
            stopped = json.loads(records.get(name.replace('-intent', '-stopped'), b'null'))
            if (not isinstance(stopped, dict) or stopped.get('verifiedStopped') is not True
                    or stopped.get('intentSHA256') != hashlib.sha256(payload).hexdigest()):
                raise ValueError('Private keychain helper needs explicit process reconciliation')
    return max(attempts, default=0)


def keychain_diagnostics(root: Path, journal) -> None:
    """Resume bounded private log retention only after verified helper closure."""
    from guest_runtime import diagnostic_snapshot, require_diagnostic
    require_keychain_stopped(journal.records())
    for name in journal.records():
        if not re.fullmatch(r'keychain-[0-9]{4}-intent.json', name):
            continue
        step = name.removesuffix('-intent.json')
        records = journal.records()
        if step + '.log' not in records or step + '-log.json' not in records:
            payload, metadata = diagnostic_snapshot(root / (step + '.log'))
            journal.put(step + '.log', payload)
            journal.put(step + '-log.json', metadata)
        require_diagnostic(journal.records(), step)


def run_keychain(root: Path, action: str, journal) -> dict:
    """Isolated HOME and owned process group, with durable crash quarantine."""
    # Keep the standalone -I Security child free of harness imports.
    from case_evidence import canonical, digest
    from host_runtime import OwnedProcess
    if action not in {'create', 'delete'} or journal is None:
        raise ValueError('Private keychain operation requires a durable journal')
    keychain_diagnostics(root, journal)
    attempt = require_keychain_stopped(journal.records()) + 1
    if attempt > 9999:
        raise ValueError('Private keychain attempt limit reached')
    name = f'keychain-{attempt:04d}'
    arguments = ['/usr/bin/python3', '-I', str(Path(__file__).resolve()), action, str(root)]
    intent = canonical({'arguments': arguments})
    child = OwnedProcess()
    output = root / (name + '.log')
    started = time.monotonic_ns()
    with output.open('xb') as log:
        journal.put(name + '-intent.json', intent)
        try:
            child.start(arguments, root, log)
            journal.put(name + '-process.json', canonical({'pid': child.process.pid}))
            code = child.process.wait(timeout=10)
        finally:
            child.stop()
            journal.put(name + '-stopped.json', canonical({'verifiedStopped': True,
                        'intentSHA256': digest(intent), 'durationNS': time.monotonic_ns() - started}))
            log.flush()
            keychain_diagnostics(root, journal)
    if code != 0:
        raise RuntimeError('Private keychain helper failed; private diagnostics retained')
    with output.open('rb') as incoming:
        result = json.loads(incoming.read(4096))
    journal.put(name + '-result.json', canonical(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'delete'])
    parser.add_argument('root', type=Path)
    options = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(operate(options.root, options.action), sort_keys=True))

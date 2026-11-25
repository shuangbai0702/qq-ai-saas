"""Resolve QQNT launched outside LLBot's parent tree from its existing proof.

No process-name scan, new process record, or ownership transfer is performed.
QQ lifetime is bound to the protocol owner, independently of AI generations.
"""
import json
import math
import os
from pathlib import Path

import psutil


def _path(value):
    return os.path.normcase(os.path.abspath(value))


def _read(file):
    if file.is_symlink():
        raise ValueError('llbot-proof-unavailable')
    with file.open('rb') as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError('llbot-proof-unavailable')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('llbot-proof-unavailable')
    return raw, value


def native_qq(row, owner):
    """Return a lifetime-pinned native root, or None for other protocols.

    An LLBot with unavailable/invalid native proof raises instead of publishing
    an apparently complete eight-process sample or reclaiming an unknown PID.
    """
    root = Path(row['root'])
    manifest_file = root/'llbot-platform.json'
    if not any(_path(arg) == _path(manifest_file) for arg in owner.cmdline()):
        return None
    _, manifest = _read(manifest_file)
    home = root/'llbot'/'qq'
    profile = home/'profile'
    proof_file = home/'profile-proof.json'
    proof_raw, proof = _read(proof_file)
    if (_path(manifest.get('root', '')) != _path(root)
            or manifest.get('botId') != root.name
            or proof.get('botId') != root.name
            or not isinstance(proof.get('profile'), str)
            or _path(proof['profile']) != _path(profile)
            or proof.get('network') is not True
            or type(proof.get('pid')) is not int or proof['pid'] <= 0):
        raise ValueError('llbot-native-owner-mismatch')
    process = psutil.Process(proof['pid'])
    born = process.create_time()
    # The bootstrap proof predates a reused PID. A previous protocol owner's
    # surviving QQ cannot be silently adopted by a newly started owner either.
    if not math.isfinite(born) or born < row['created'] or born > proof_file.stat().st_mtime:
        raise ValueError('llbot-native-lifetime-mismatch')
    if _path(process.exe()) != _path(home/'program'/'QQ.exe'):
        raise ValueError('llbot-native-owner-mismatch')
    profiles = [arg.split('=', 1)[1].strip('"') for arg in process.cmdline()
                if arg.startswith('--user-data-dir=')]
    if len(profiles) != 1 or _path(profiles[0]) != _path(profile):
        raise ValueError('llbot-native-owner-mismatch')
    current = psutil.Process(process.pid)
    if abs(current.create_time()-born) >= .01 or proof_file.read_bytes() != proof_raw:
        raise ValueError('llbot-native-lifetime-mismatch')
    return process, born

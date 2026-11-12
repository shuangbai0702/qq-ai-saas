"""Copy an explicit frozen file inventory into a NEW, offline generation.

Default: plan only (no files/directories created).
  python scripts/mobile-file-delta.py SOURCE_DIR TARGET_GENERATION MANIFEST.json
  python scripts/mobile-file-delta.py SOURCE_DIR TARGET_GENERATION MANIFEST.json --apply

Manifest schema (no wildcards or directory entries):
 {"schemaVersion":1,"snapshot":{"id":"operator-freeze-id","frozen":true,
   "method":"stopped-read-only","observedAt":"2026-11-12T08:00:00Z"},
  "deletionPolicy":"no-age-only-deletion",
  "files":[{"path":"bots/opaque/behavior.sqlite","size":123,"sha256":"...",
            "necessary":true,"reason":"Preserve this bot's complete frozen business ledger byte for byte"}]}

Paths use portable '/' separators. TARGET_GENERATION's parent must already
exist. The generation must be absent, or carry this tool's exact same manifest
marker from an earlier run. Never point it at a live runtime. Ordinary existing
directories are rejected, even if some files happen to match. Known, matching
files in a resumable generation are reused; mismatches and unknown entries fail.

The manifest MUST come from an independently verified stopped, frozen snapshot,
including a consistent SQLite/WAL closure, original Journal, unknown/partial
ledgers, and all necessary referenced files. This tool does NOT determine that
business closure, prove the claimed freeze, interpret/rewrite databases, clean
by age, stop/start services, or grant execution/routing authority. It copies
explicit bytes only. A successful copy is not permission or proof of cutover.
Keep the source and offline generation under exclusive operator control during
the run: checks reject links/reparse points but do not replace OS isolation
against a malicious concurrent directory-renaming process.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

MARKER = '.mobile-file-delta-generation.json'
STAGE = '.mobile-file-delta-stage-'


class DeltaError(ValueError):
    """Stable codes never disclose paths, reasons, or business content."""


def require(condition, code):
    if not condition:
        raise DeltaError(code)


def load_manifest(path):
    path = no_links(path)
    require(stat.S_ISREG(path.lstat().st_mode), 'nonregular_file_refused')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_manifest_key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs)


def validate_manifest(value):
    require(isinstance(value, dict) and set(value) == {'schemaVersion', 'snapshot', 'deletionPolicy', 'files'}, 'invalid_manifest')
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1, 'unsupported_schema')
    require(value['deletionPolicy'] == 'no-age-only-deletion', 'unsafe_deletion_policy')
    snap = value['snapshot']
    require(isinstance(snap, dict) and set(snap) == {'id', 'frozen', 'method', 'observedAt'}, 'missing_frozen_evidence')
    require(isinstance(snap['id'], str) and bool(snap['id'].strip()) and len(snap['id']) <= 256 and snap['frozen'] is True and snap['method'] == 'stopped-read-only', 'missing_frozen_evidence')
    try:
        at = datetime.fromisoformat(snap['observedAt'].replace('Z', '+00:00'))
        require(at.tzinfo is not None and at.utcoffset() is not None, 'invalid_observation_time')
    except (ValueError, TypeError, AttributeError):
        raise DeltaError('invalid_observation_time') from None
    require(isinstance(value['files'], list) and bool(value['files']), 'empty_file_inventory')
    paths = set()
    for item in value['files']:
        require(isinstance(item, dict) and set(item) == {'path', 'size', 'sha256', 'necessary', 'reason'}, 'invalid_file_entry')
        path = item['path']
        require(isinstance(path, str) and bool(path) and not any(c in path for c in '\\:*?[]{}') and all(ord(c) >= 32 for c in path), 'unsafe_path')
        parts = path.split('/')
        require(all(p and p not in ('.', '..') and not p.endswith(('.', ' ')) for p in parts), 'unsafe_path')
        require(all(not re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) and not p.casefold().startswith('.mobile-file-delta-') for p in parts), 'unsafe_path')
        folded = path.casefold()
        require(folded not in paths, 'duplicate_or_case_colliding_path')
        paths.add(folded)
        require(type(item['size']) is int and item['size'] >= 0 and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']) is not None, 'invalid_file_digest_or_size')
        require(item['necessary'] is True and isinstance(item['reason'], str) and 8 <= len(item['reason'].strip()) <= 1000, 'necessity_reason_required')
    require(all('/'.join(p.split('/')[:n]) not in paths for p in paths for n in range(1, len(p.split('/')))), 'file_directory_collision')
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def no_links(path, missing=False):
    """Reject symlinks and Windows junction/reparse points in every ancestor."""
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            require(missing, 'path_missing')
            continue
        require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)), 'link_or_reparse_point_refused')
    return path


def fingerprint(path):
    no_links(path)
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode), 'nonregular_file_refused')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and (before.st_dev, before.st_ino) == (info.st_dev, info.st_ino), 'file_changed_during_read')
        h = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) == (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev), 'file_changed_during_read')
    return after.st_size, h


def matches(path, item):
    return fingerprint(path) == (item['size'], item['sha256'])


def marker_value(manifest_hash):
    return {'tool': 'mobile-file-delta-v1', 'manifestSha256': manifest_hash, 'executionAuthorized': False}


def inspect_generation(target, entries, marker):
    no_links(target, missing=True)
    if not target.exists():
        return set()
    require(target.is_dir(), 'target_not_directory')
    marker_path = target / MARKER
    require(marker_path.exists(), 'existing_unowned_generation')
    no_links(marker_path)
    try:
        require(json.loads(marker_path.read_text(encoding='utf-8')) == marker, 'generation_manifest_mismatch')
    except (ValueError, UnicodeError):
        raise DeltaError('generation_manifest_mismatch') from None
    allowed = {item['path'] for item in entries}
    dirs = {''}
    for path in allowed:
        dirs.update('/'.join(path.split('/')[:n]) for n in range(1, len(path.split('/'))))
    existing = set()
    # Only inspect this owned offline generation; never scan source/runtime.
    for directory in sorted(dirs):
        parent = target / directory
        no_links(parent, missing=True)
        if not parent.exists():
            continue
        require(parent.is_dir(), 'unexpected_generation_entry')
        for child in parent.iterdir():
            no_links(child)
            relative = child.relative_to(target).as_posix()
            if relative == MARKER:
                require(child.is_file(), 'unexpected_generation_entry')
            elif relative in dirs:
                require(child.is_dir(), 'unexpected_generation_entry')
            else:
                require(relative in allowed and child.is_file(), 'unexpected_generation_entry')
                existing.add(relative)
    by_path = {item['path']: item for item in entries}
    require(all(matches(target/path, by_path[path]) for path in existing), 'existing_target_hash_mismatch')
    return existing


def copy_cas(source, destination, item):
    no_links(source)
    no_links(destination, missing=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    no_links(destination.parent)
    stage = None
    try:
        with tempfile.NamedTemporaryFile(prefix=STAGE, dir=destination.parent, delete=False) as output:
            stage = Path(output.name)
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            with os.fdopen(os.open(source, flags), 'rb') as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'nonregular_file_refused')
                while chunk := stream.read(1024*1024):
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        require(matches(stage, item), 'source_changed_or_copy_hash_mismatch')
        no_links(destination, missing=True)
        try:
            # Atomic no-clobber publication. No replace()/overwrite fallback.
            os.link(stage, destination)
        except FileExistsError:
            require(matches(destination, item), 'target_changed_during_copy')
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)  # only the temporary file we created


def transfer(source, target, manifest, apply=False):
    manifest_hash = validate_manifest(manifest)
    source, target = no_links(source), no_links(target, missing=True)
    require(source.is_dir() and target.parent.is_dir(), 'invalid_root_directory')
    require(not source.is_relative_to(target) and not target.is_relative_to(source), 'overlapping_roots_refused')
    entries = manifest['files']
    # Preflight ALL source bytes and target entries before any write.
    require(all(matches(source/item['path'], item) for item in entries), 'source_hash_mismatch')
    marker = marker_value(manifest_hash)
    existing = inspect_generation(target, entries, marker)
    copied = 0
    if apply:
        if not target.exists():
            target.mkdir(mode=0o700)  # no exist_ok: competing owner must fail
            with (target/MARKER).open('x', encoding='utf-8') as stream:
                json.dump(marker, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        # Recheck ownership and directory closure immediately before copies.
        existing = inspect_generation(target, entries, marker)
        for item in entries:
            if item['path'] not in existing:
                require(json.loads((target/MARKER).read_text(encoding='utf-8')) == marker, 'generation_manifest_mismatch')
                copy_cas(source/item['path'], target/item['path'], item)
                copied += 1
        # Independent final read/hash of every specified target file.
        require(len(inspect_generation(target, entries, marker)) == len(entries), 'final_inventory_incomplete')
        require(all(matches(target/item['path'], item) for item in entries), 'final_target_hash_mismatch')
    return {'mode': 'applied' if apply else 'plan', 'files': len(entries),
            'wouldCopy': len(entries)-len(existing), 'copied': copied, 'reused': len(existing),
            'allTargetFilesVerified': bool(apply), 'executionAuthorized': False,
            'freezeProven': False, 'businessClosureProven': False,
            'limitation': 'Explicit file bytes only; independent frozen database and reference closure evidence and a separate authorized cutover are required.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source_dir')
    parser.add_argument('target_generation')
    parser.add_argument('source_manifest')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = transfer(args.source_dir, args.target_generation, load_manifest(args.source_manifest), args.apply)
    except DeltaError as error:
        result = {'error': str(error), 'executionAuthorized': False, 'allTargetFilesVerified': False}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        result = {'error': 'filesystem_or_manifest_read_failed', 'errorType': type(error).__name__, 'executionAuthorized': False, 'allTargetFilesVerified': False}
    print(json.dumps(result, separators=(',', ':')))
    return 1 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(main())

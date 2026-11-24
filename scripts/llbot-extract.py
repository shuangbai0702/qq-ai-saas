"""Extract only reviewed runtime assets; never launch bundled executables/scripts."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

EXPECTED = 'cd13e20c8790fff02029153560a5467db333e10f7bec63bf77052c20cce4786c'


def extract(archive, destination):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('Expected the reviewed 8.3.2 test archive')
    if destination.exists():
        raise ValueError('Installation destination already exists')
    with zipfile.ZipFile(archive) as source:
        selected = []
        for entry in source.infolist():
            name = PurePosixPath(entry.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in entry.filename:
                raise ValueError('Invalid archive path')
            if not name.is_relative_to('LLBot-Local'):
                continue
            relative = name.relative_to('LLBot-Local')
            if entry.is_dir():
                continue
            allowed = str(relative).startswith('bin/llbot/') or str(relative) in {
                'bin/pmhq/pmhq.exe', 'bin/pmhq/pmhq.dll',
                'bin/pmhq/pmhq_loader.js', 'bin/pmhq/pmhq_config.json',
                'bin/pmhq/auth-local/leaf.pem', 'bin/pmhq/auth-local/leaf-key.pem',
                'bin/pmhq/auth-local/isrg-spki-pattern.bin', 'bin/pmhq/auth-local/spki-inner-546.der',
                'bin/pmhq/auth-local/sign-info.json',
            }
            if allowed:
                selected.append((entry, relative))
        for entry, relative in selected:
            output = destination.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(source.read(entry))
    return len(selected)


if __name__ == '__main__':
    try:
        print(json.dumps({'files': extract(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())}))
    except Exception as error:
        print('LLBot runtime extraction failed: ' + str(error), file=sys.stderr)
        sys.exit(1)

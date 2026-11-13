"""Offline only. Input: reviewed clean source tree and explicit target list."""
from pathlib import Path
import json,hashlib,sys
base=Path(__file__).resolve().parent
assert len(sys.argv)==3,'prepare-release.py CLEAN_SOURCE_ROOT TARGETS_JSON'
root=Path(sys.argv[1]).resolve();targets=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
assert isinstance(targets,list) and targets and len(targets)==len(set(targets))
inventory=json.loads((base/'inventory.json').read_text(encoding='utf-8'))
build=json.loads((root/'dist-server/build.json').read_text(encoding='utf-8'))
assert not build['gitDirty'],'Review/build a clean release source before packaging'
assert 'dist-server/apps/node-agent/src/maintenance.js' in targets
assert 'dist-server/build.json' in targets
for n in targets:
 part=Path(n);assert not part.is_absolute() and '..' not in part.parts and chr(92) not in n
 assert (root/part).is_file(),n
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
m={'commit':build['gitCommit'],'productionBefore':inventory['files'],'targets':{n:{'sha256':sha(root/n)}for n in targets},'capturedAt':inventory['capturedAt']}
candidate=Path('experiments/media-production-20261113/media-resources.candidate.json');expected='75bc295c63101164dbd7b85c1161de5aa3d5507d4b8c7fe282c218cc9e6017c2';assert sha(candidate)==expected
m['media']={'sha256':expected,'before':inventory['mediaBefore']}
(base/'media-resources.json').write_bytes(candidate.read_bytes())
(base/'manifest.json').write_text(json.dumps(m),encoding='utf-8');(base/'build.json').write_text(json.dumps(build),encoding='utf-8')
(base/'release-input.json').write_text(json.dumps({'sourceRoot':str(root),'commit':build['gitCommit']}),encoding='utf-8')
print(json.dumps({'prepared':True,'commit':build['gitCommit'],'targets':len(targets),'manifestSha256':sha(base/'manifest.json')}))

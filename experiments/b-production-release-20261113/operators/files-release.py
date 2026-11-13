"""Identity/hash-checked program overlay. Does not edit business databases."""
import json,hashlib,os,sys
from pathlib import Path
BASE=Path('C:/FishBotPredeploy20261112');DIR=BASE/'control/b-release-20261113'
cfg=json.loads((BASE/'mobile-private-config.json').read_text(encoding='utf-8-sig'));root=Path(cfg['sourceRoot']);m=json.loads((DIR/'manifest.json').read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
def norm(b):return sha(b.replace(b'\r\n',b'\n'))
media=Path(cfg['dataRoot'])/'media-resources.json'
assert media.parent.resolve()==Path(cfg['dataRoot']).resolve() and media.name=='media-resources.json'
def mediahash():return sha(media.read_bytes())if media.exists()else None
def check():
 assert mediahash()==m['media']['before'],'Concurrent media configuration drift'
 assert sha((DIR/'media-resources.json').read_bytes())==m['media']['sha256']
 assert {p.name:norm(p.read_bytes())for p in(root/'adapters/astrbot').glob('*.py')}==m['productionBefore'],'Concurrent Python source drift'
 build=json.loads((DIR/'build.json').read_text())
 for name,digest in build['sourceHashes'].items():
  if name not in m['targets']:assert sha((root/name).read_bytes())==digest,'Unreviewed TypeScript source drift: '+name
 for name,v in m['targets'].items():assert sha((DIR/'staged'/name).read_bytes())==v['sha256']
def write(p,b):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.b-release.tmp');assert not tmp.exists()
 with tmp.open('xb')as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.replace(tmp,p);assert p.read_bytes()==b
op=sys.argv[1]
if op=='check':check();print(json.dumps({'ok':True,'targets':len(m['targets'])}))
elif op=='install':
 check();fence=json.loads((DIR/'hold-result.json').read_text());baseline=json.loads((DIR/'baseline.json').read_text());assert fence['ok'] and fence['atomicFences']==len(baseline['ais'])
 permit=json.loads((DIR/'backup-permit.json').read_text());proof=json.loads((DIR/'backup-result.json').read_text());assert proof['ok'] and proof['complete'] and proof['permit']==permit['token'] and proof['heldAgentPid']==fence['pid'] and proof['holdCapturedAt']==baseline['capturedAt']
 backup=Path(proof['directory']).resolve();assert backup.is_relative_to((Path(cfg['stateRoot'])/'backups').resolve());assert sha((backup/'manifest-private.json').read_bytes())==proof['manifestSha256']
 assert proof['postgresVerified'] and proof['aclRestricted'] and proof['vssRemoved']
 assert not(DIR/'transaction.json').exists(),'Existing transaction'
 tx={'commit':m['commit'],'before':{},'after':{n:v['sha256']for n,v in m['targets'].items()},'status':'prepared','mediaBefore':mediahash(),'mediaAfter':m['media']['sha256']}
 for name in m['targets']:
  p=root/name;before=p.read_bytes()if p.exists()else None;tx['before'][name]=sha(before)if before is not None else None
  if before is not None:
   backup=DIR/'backup'/name;backup.parent.mkdir(parents=True,exist_ok=True)
   with backup.open('xb')as f:f.write(before)
 if tx['mediaBefore'] is not None:
  backup=DIR/'backup/media-resources.json';backup.parent.mkdir(parents=True,exist_ok=True)
  with backup.open('xb')as f:f.write(media.read_bytes())
 (DIR/'transaction.json').write_text(json.dumps(tx))
 for name in m['targets']:
  p=root/name;assert (sha(p.read_bytes())if p.exists()else None)==tx['before'][name]
  write(p,(DIR/'staged'/name).read_bytes())
 assert mediahash()==tx['mediaBefore'];write(media,(DIR/'media-resources.json').read_bytes())
 tx['status']='installed';(DIR/'transaction.json').write_text(json.dumps(tx));print(json.dumps({'ok':True,'commit':m['commit'],'installed':len(m['targets'])}))
elif op=='restore':
 if not(DIR/'transaction.json').exists():print(json.dumps({'ok':True,'restored':0,'noTransaction':True}));sys.exit()
 tx=json.loads((DIR/'transaction.json').read_text())
 assert mediahash()in(tx['mediaBefore'],tx['mediaAfter']),'Concurrent media replacement'
 for n,before in tx['before'].items():
  p=root/n;assert (sha(p.read_bytes())if p.exists()else None)in(before,tx['after'][n]),'Concurrent file replacement'
 for n,before in tx['before'].items():
  p=root/n
  if before is None:
   if p.exists():assert sha(p.read_bytes())==tx['after'][n];p.unlink()
  else:
   b=(DIR/'backup'/n).read_bytes();assert sha(b)==before;write(p,b)
 if tx['mediaBefore'] is None:
  if media.exists():assert mediahash()==tx['mediaAfter'];media.unlink()
 else:
  original=(DIR/'backup/media-resources.json').read_bytes();assert sha(original)==tx['mediaBefore'];write(media,original)
 tx['status']='restored';(DIR/'transaction.json').write_text(json.dumps(tx));print(json.dumps({'ok':True,'restored':len(tx['before'])}))
elif op=='attest':
 assert mediahash()==m['media']['sha256']
 assert all(sha((root/n).read_bytes())==v['sha256']for n,v in m['targets'].items())
 untouched={p.name:norm(p.read_bytes())for p in(root/'adapters/astrbot').glob('*.py')if 'adapters/astrbot/'+p.name not in m['targets']}
 assert untouched=={n:v for n,v in m['productionBefore'].items()if 'adapters/astrbot/'+n not in m['targets']}
 print(json.dumps({'ok':True,'commit':m['commit'],'matched':len(m['targets']),'untouchedPython':len(untouched)}))
else:raise ValueError(op)

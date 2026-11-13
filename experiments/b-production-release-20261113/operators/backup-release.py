"""Held-release private backups. Never restores over live business state."""
from pathlib import Path
import base64,hashlib,json,os,sqlite3,subprocess,sys,time,uuid
from contextlib import closing

BASE=Path('C:/FishBotPredeploy20261112');CONTROL=BASE/'control/b-release-20261113'

def sha(path):
 with path.open('rb')as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def powershell(body):
 code="$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"+body
 result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(code.encode('utf-16le')).decode()],capture_output=True,timeout=120,creationflags=0x08000000)
 if result.returncode:raise RuntimeError('Protected backup operation failed; private command output withheld')
 return json.loads(result.stdout.decode('utf-8-sig'))

def private_directory(path):
 path.mkdir(parents=True,exist_ok=False)
 quoted=str(path).replace("'","''")
 proof=powershell("$p='"+quoted+"';$acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetAccessRuleProtection($true,$false);$current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;$ids=@('S-1-5-18','S-1-5-32-544',$current)|Select-Object -Unique;foreach($sid in $ids){$r=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow');$acl.AddAccessRule($r)};Set-Acl -LiteralPath $p -AclObject $acl;$a=Get-Acl -LiteralPath $p;@{protected=$a.AreAccessRulesProtected;allowed=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|ForEach-Object {$_.IdentityReference.Value})}|ConvertTo-Json -Compress")
 assert proof['protected'] and set(proof['allowed']).issubset({'S-1-5-18','S-1-5-32-544',powershell("@{sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}|ConvertTo-Json -Compress")['sid']})
 return True

def is_reparse(path):
 return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)

def bounded(path,root):
 root=root.resolve();candidate=path.resolve()
 assert candidate.is_relative_to(root) and candidate!=root,'Backup input escaped approved root'
 current=path
 while current!=root:
  assert not is_reparse(current),'Reparse point in backup source'
  current=current.parent
 return candidate

def sqlite_counts(db):
 return {name:int(db.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0])for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")}

def sqlite_backup(source,target):
 # SQLite backup API includes committed WAL pages without copying a torn file.
 with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=10))as live,closing(sqlite3.connect(target))as saved:
  live.backup(saved,pages=128,sleep=0.02)
  assert saved.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
  counts=sqlite_counts(saved)
 # Restore verification is a second independent SQLite database, not a live
 # overwrite. Keep both files in the protected release backup directory.
 checked=target.with_suffix('.restored.sqlite')
 with closing(sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True))as saved,closing(sqlite3.connect(checked))as restored:
  saved.backup(restored);assert restored.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
  assert sqlite_counts(restored)==counts
 return {'bytes':target.stat().st_size,'sha256':sha(target),'tableCounts':counts,'restoreVerified':True,'restoredSha256':sha(checked)}

def copy_tree(source,target):
 """Only a named persistent subtree of an invocation-owned VSS snapshot."""
 assert source.is_dir() and not is_reparse(source)
 records=[]
 for folder,dirs,files in os.walk(source,followlinks=False):
  parent=Path(folder)
  for name in dirs:
   candidate=parent/name;assert not is_reparse(candidate),'Snapshot subtree contains a reparse point'
  for name in files:
   candidate=parent/name;assert not is_reparse(candidate)
   relative=candidate.relative_to(source);dest=target/relative;dest.parent.mkdir(parents=True,exist_ok=True)
   with candidate.open('rb')as src,dest.open('xb')as dst:
    while chunk:=src.read(1024*1024):dst.write(chunk)
   digest=sha(candidate);assert sha(dest)==digest
   records.append({'path':relative.as_posix(),'bytes':dest.stat().st_size,'sha256':digest})
 return records

phase='init'
def main():
 global phase
 cfg=json.loads((BASE/'mobile-private-config.json').read_text(encoding='utf-8-sig'));baseline=json.loads((CONTROL/'baseline.json').read_text());hold=json.loads((CONTROL/'hold-result.json').read_text())
 assert hold['ok'] and hold['held'] and hold['atomicFences']==len(baseline['ais'])
 permit=json.loads((CONTROL/'backup-permit.json').read_text());assert permit['pid']==hold['pid']==baseline['agent']['pid'] and permit['fenced'] and permit['baselineCapturedAt']==baseline['capturedAt']
 assert time.time()-permit['at']<120,'Backup permit expired'
 assert json.loads((Path(cfg['stateRoot'])/'services/agent.process.json').read_text(encoding='utf-8-sig'))==baseline['agent']
 data=Path(cfg['dataRoot']).resolve();assert data.is_absolute() and not is_reparse(data)
 parent=Path(cfg['stateRoot']).resolve()/'backups';parent.mkdir(exist_ok=True);assert not is_reparse(parent) and parent.resolve().parent==Path(cfg['stateRoot']).resolve()
 out=parent/('b-release-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex)
 phase='private-acl'
 acl=private_directory(out)
 result={'kind':'b-release-private-backup','aclRestricted':acl,'sqlite':[],'qq':[],'complete':False,'businessRestorePolicy':'Never auto-restore business databases during code rollback; later messages may exist.'}
 # Critical key/config are kept private; they are required to use restored PG secrets.
 secrets=[]
 for source,label in [(data/'master.key','master.key'),(BASE/'mobile-private-config.json','deployment-private-config.json')]:
  assert source.is_file() and not source.is_symlink();dest=out/label
  with dest.open('xb')as stream:stream.write(source.read_bytes())
  secrets.append({'label':label,'sha256':sha(dest),'bytes':dest.stat().st_size})
 result['protectedSecrets']=secrets
 phase='postgres-dump-restore'
 pg=subprocess.run([cfg['node'],str(CONTROL/'backup-pg.mjs'),str(out)],capture_output=True,timeout=900,creationflags=0x08000000)
 if pg.returncode:raise RuntimeError('Consistent PostgreSQL dump/isolated restore failed')
 result['postgres']=json.loads((out/'postgres-manifest.json').read_text())
 # Enumerate only explicit baseline-owned bot paths. Main Journal is persisted
 # in behavior.sqlite; do not search other users, dependencies or the disk.
 phase='sqlite'
 for index,bot in enumerate(baseline.get('backupBots',baseline['running'])):
  if not (data/'bots'/bot['id']).exists():
   assert bot['id'] not in {x['id']for x in baseline['running']};result['sqlite'].append({'slot':index,'status':'no-persisted-runtime'});continue
  botroot=bounded(data/'bots'/bot['id'],data/'bots')
  if not(botroot/'behavior.sqlite').exists():
   assert bot['id'] not in {x['id']for x in baseline['running']};result['sqlite'].append({'slot':index,'status':'no-persisted-behavior'});continue
  source=bounded(botroot/'behavior.sqlite',data/'bots');assert source.is_file(),'Main Journal/behavior database missing'
  result['sqlite'].append({'slot':index,**sqlite_backup(source,out/('behavior-'+str(index)+'.sqlite'))})
 # QQ native login files remain in-use. A volume snapshot provides a single
 # crash-consistent byte view without stopping NapCat or clearing its state.
 phase='vss-qq'
 volume=data.anchor;shadow=None
 try:
  v=volume.replace("'","''");receipt=out/'vss-created.json';receiptQuoted=str(receipt).replace("'","''")
  shadow=powershell("$r=Invoke-CimMethod -ClassName Win32_ShadowCopy -MethodName Create -Arguments @{Volume='"+v+"';Context='ClientAccessible'};if($r.ReturnValue -ne 0){throw 'VSS snapshot failed'};@{id=$r.ShadowID}|ConvertTo-Json -Compress|Set-Content -LiteralPath '"+receiptQuoted+"' -Encoding UTF8;$s=Get-CimInstance Win32_ShadowCopy|Where-Object {$_.ID -eq $r.ShadowID};@{id=$s.ID;device=$s.DeviceObject}|ConvertTo-Json -Compress")
  assert shadow['id'] and shadow['device'].startswith('\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy')
  snapshot=Path(shadow['device']+'\\')
  result['vssVolume']=volume;result['vssSnapshotId']=shadow['id'];result['vssDevice']=shadow['device']
  for index,bot in enumerate(baseline.get('backupBots',baseline['running'])):
   if bot.get('protocol')!='napcat':continue
   if not(data/'bots'/bot['id']/'napcat-platform.json').exists():
    assert bot['id'] not in {x['id']for x in baseline['running']};result['qq'].append({'slot':index,'status':'no-persisted-napcat-manifest'});continue
   botroot=bounded(data/'bots'/bot['id'],data/'bots')
   manifestPath=bounded(botroot/'napcat-platform.json',data/'bots');platform=json.loads(manifestPath.read_text(encoding='utf-8'))
   assert platform['botId']==bot['id'];qq=bounded(Path(platform['dataDir']),data/'bots')
   assert qq==botroot/'qq' and qq.is_dir(),'Unrecognized QQ persistence owner'
   result.setdefault('qqRuntimeRoots',[]).append({'slot':index,'dataDir':str(qq),'runtimeDir':platform.get('runtimeDir'),'manifestSha256':sha(manifestPath)})
   copied=[]
   for leaf in ['profile','home','napcat/config']:
    live=bounded(qq/leaf,data/'bots');assert live.is_dir(),'Known QQ persistent directory missing'
    source=snapshot/live.relative_to(Path(volume));records=copy_tree(source,out/'qq'/str(index)/leaf)
    copied.append({'subtree':leaf,'files':records,'fileCount':len(records),'bytes':sum(r['bytes']for r in records)})
   identity=qq/'identity.json'
   if not identity.exists():
    assert bot['id'] not in {x['id']for x in baseline['running']},'Running QQ identity missing'
    result['qq'].append({'slot':index,'subtrees':copied,'identityStatus':'absent-before-release','consistency':'VSS disk crash-consistent; no login replay performed'});continue
   identity=bounded(identity,data/'bots');assert identity.is_file()
   source=snapshot/identity.relative_to(Path(volume));dest=out/'qq'/str(index)/'identity.json';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(source.read_bytes());assert sha(dest)==sha(source)
   result['qq'].append({'slot':index,'subtrees':copied,'identitySha256':sha(dest),'consistency':'VSS disk crash-consistent; no login replay performed'})
 finally:
  if shadow is None and (out/'vss-created.json').exists():shadow=json.loads((out/'vss-created.json').read_text(encoding='utf-8-sig'))
  if shadow:
   sid=shadow['id'];assert len(sid)==38 and sid.startswith('{') and sid.endswith('}')
   removed=powershell("$s=Get-CimInstance Win32_ShadowCopy|Where-Object {$_.ID -eq '"+sid+"'};if($null -eq $s){throw 'Owned VSS snapshot disappeared'};Remove-CimInstance -InputObject $s;@{removed=(@(Get-CimInstance Win32_ShadowCopy|Where-Object {$_.ID -eq '"+sid+"'}).Count -eq 0)}|ConvertTo-Json -Compress");assert removed['removed']
 phase='finalize'
 result['vssRemoved']=True;result['complete']=True
 (out/'manifest-private.json').write_text(json.dumps(result),encoding='utf-8')
 proof={'ok':True,'complete':True,'heldAgentPid':baseline['agent']['pid'],'holdCapturedAt':baseline['capturedAt'],'permit':permit['token'],'directory':str(out),'manifestSha256':sha(out/'manifest-private.json'),'postgresVerified':True,'sqliteVerified':len(result['sqlite']),'qqBackedUp':len(result['qq']),'aclRestricted':True,'vssRemoved':True}
 (CONTROL/'backup-result.json').write_text(json.dumps(proof),encoding='utf-8')
 print(json.dumps(proof))

if __name__=='__main__':
 try:main()
 except BaseException as error:
  # Do not log file paths/contents, connection arguments or provider secrets.
  import traceback
  proof={'ok':False,'phase':phase,'errorType':type(error).__name__,'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}for f in traceback.extract_tb(error.__traceback__)]}
  (CONTROL/'backup-error.json').write_text(json.dumps(proof),encoding='utf-8')
  print(json.dumps(proof),flush=True);sys.exit(1)

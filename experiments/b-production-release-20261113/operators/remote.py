from pathlib import Path
import sys,json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0,str(Path('.local/mobile-predeploy-20261112').resolve()))
from upload import connect
local=Path('.local/b-release-20261113');remote='C:/FishBotPredeploy20261112/control/b-release-20261113';op=sys.argv[1]
with connect()as c:
 s=c.open_sftp()
 if op in ('stage','stage-operators'):
  def mkdir(path):
   try:s.stat(path)
   except FileNotFoundError:mkdir(path.rsplit('/',1)[0]);s.mkdir(path)
  mkdir(remote+'/staged')
  names=['operator-release.mjs','manage-release.mjs','invoke-release.ps1','files-release.py','manifest.json','build.json','restart-api.py','restart-api.ps1','media-resources.json','backup-pg.mjs','backup-release.py','backup-release.ps1']
  if op=='stage-operators':names=['operator-release.mjs','manage-release.mjs','invoke-release.ps1']
  for n in names:s.put(str(local/n),remote+'/'+n)
  if op=='stage-operators':print(json.dumps({'ok':True,'operatorsStaged':True}));sys.exit()
  clean=Path(json.loads((local/'release-input.json').read_text())['sourceRoot']).resolve()
  for n in json.loads((local/'manifest.json').read_text())['targets']:
   mkdir((remote+'/staged/'+n).rsplit('/',1)[0]);s.put(str(clean/n),remote+'/staged/'+n)
  print(json.dumps({'ok':True,'staged':len(json.loads((local/'manifest.json').read_text())['targets'])}));sys.exit()
 cmd=('C:/Python311/python.exe '+remote+'/files-release.py '+op)if op in('check','install','restore','attest')else('powershell.exe -NoProfile -ExecutionPolicy Bypass -File '+remote+'/invoke-release.ps1 -Operation '+op)
 if op in ('backup','backup-stopped'):cmd='powershell.exe -NoProfile -ExecutionPolicy Bypass -File '+remote+'/backup-release.ps1'+(' -CheckStopped' if op=='backup-stopped' else '')
 if op=='restart-api':cmd='powershell.exe -NoProfile -ExecutionPolicy Bypass -File '+remote+'/restart-api.ps1'
 i,o,e=c.exec_command(cmd,timeout=1500 if op=='backup' else 400);out=o.read();err=e.read();status=o.channel.recv_exit_status();(local/(op+'-stdout.json')).write_bytes(out);(local/(op+'-stderr.log')).write_bytes(err)
 if status:print(out.decode('utf8',errors='replace'));print(err.decode('utf8',errors='replace'));raise SystemExit(status)
 result=json.loads(out.decode('utf-8-sig'));assert result.get('ok',result.get('apiReady'));print(json.dumps(result))
 if op=='baseline':
  for n in ['baseline.json','inventory.json','service-identities.json']:s.get(remote+'/'+n,str(local/n))

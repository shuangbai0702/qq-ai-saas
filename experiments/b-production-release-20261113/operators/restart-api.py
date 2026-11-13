from pathlib import Path
import json,sys,time,urllib.request,traceback
b=Path('C:/FishBotPredeploy20261112');cfg=json.loads((b/'mobile-private-config.json').read_text(encoding='utf-8-sig'));sys.path.insert(0,str(Path(cfg['sourceRoot'])/'scripts'));from runtime_memory import owned
result={}
try:
 before=json.loads((b/'control/b-release-20261113/service-identities.json').read_text());record=Path(cfg['stateRoot'])/'services/control-api.process.json';current=json.loads(record.read_text(encoding='utf-8-sig'));priorProof=b/'control/b-release-20261113/api-restart-proof.json';prior=json.loads(priorProof.read_text())if priorProof.exists()else {};assert current==before['control-api'] or current==prior.get('newApiRecord');assert Path(current['root']).resolve()==Path(cfg['sourceRoot']).resolve();p=owned(current);p.terminate();result['oldApiPid']=current['pid']
 until=time.monotonic()+40
 while time.monotonic()<until:
  time.sleep(1)
  try:
   fresh=json.loads(record.read_text(encoding='utf-8-sig'));assert fresh!=current;owned(fresh)
   with urllib.request.urlopen('http://127.0.0.1:'+str(cfg['environment'].get('PORT','8787'))+'/v1/health',timeout=2)as r:assert r.status==200
   result['newApiPid']=fresh['pid'];result['newApiRecord']=fresh;break
  except (OSError,ValueError,AssertionError):pass
 assert result.get('newApiPid'),'API restart not confirmed'
 for name in ('agent','postgres'):
  fresh=json.loads((Path(cfg['stateRoot'])/'services'/(name+'.process.json')).read_text(encoding='utf-8-sig'));assert fresh==before[name];owned(fresh)
 result.update(apiReady=True,agentUnchanged=True,postgresUnchanged=True)
except Exception as e:result.update(errorType=type(e).__name__,success=False);raise
finally:(b/'control/b-release-20261113/api-restart-proof.json').write_text(json.dumps(result))

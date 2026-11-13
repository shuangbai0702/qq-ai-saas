from pathlib import Path
import json,hashlib,sqlite3,sys,urllib.request,time
b=Path('C:/FishBotPredeploy20261112');d=b/'control/b-release-20261113';cfg=json.loads((b/'mobile-private-config.json').read_text(encoding='utf-8-sig'));root=Path(cfg['sourceRoot']);data=Path(cfg['dataRoot']);baseline=json.loads((d/'baseline.json').read_text());build=json.loads((root/'dist-server/build.json').read_text());assert build['gitCommit']=='b2a1542c317278693473c59c286e37ad71bcb807' and not build['gitDirty']
sys.path.insert(0,str(root/'adapters/astrbot'));import journal_storage
result={'ok':True,'commit':build['gitCommit'],'builtAt':build['builtAt'],'gitDirty':build['gitDirty'],'loadedFunctions':[],'journals':{'same':0,'appended':0,'changedNeedsReview':0,'new':0},'activeTestMessagesSent':0}
for group in baseline['ais']:
 log=data/'bots'/group['id']/'runtime.log';text=log.read_text(encoding='utf-8',errors='replace');fresh=text.rsplit('source=ProcessHost.start',1)[-1];lines=[x.split('FISHBOT_RUNTIME_BUILD ',1)[1]for x in fresh.splitlines()if 'FISHBOT_RUNTIME_BUILD 'in x];assert lines,'Fresh runtime attestation missing';att=json.loads(lines[-1]);assert att and all(x['sourceMatchesLoaded']for x in att.values());assert 'behavior'in att and 'autonomous_runtime'in att
 for name,value in att.items():
  source='adapters/astrbot/'+name.split('.')[0]+'.py'
  assert value['sourceSha']==build['pythonSources'][source]
 result['loadedFunctions'].append({'count':len(att),'allSourceMatchesLoaded':True,'BEntryBehaviorLoaded':True,'autonomousRuntimeLoaded':True,'deltaHelperLazyLoaded':'activity_context'in att})
proof=json.loads((d/'backup-result.json').read_text());saved=Path(proof['directory'])
def rows(db):
 tables={x[0]for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 if 'fishbot_readonly_journals'not in tables:return {}
 return {r[0]:r[1:]for r in db.execute('SELECT scope,version,state,payload,sha FROM fishbot_readonly_journals')}
def readwire(db,scope,row):
 version,state,payload,digest=row
 if not payload:return None
 assert hashlib.sha256(payload.encode()).hexdigest()==digest
 if version==1:return json.loads(payload)['wire']
 def blob(s,h):
  r=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(s,h)).fetchone();assert r;return r[0]
 return journal_storage.decode(payload,scope,blob)['wire']
for i,bot in enumerate(baseline.get('backupBots',baseline['running'])):
 original=saved/('behavior-'+str(i)+'.sqlite');current=data/'bots'/bot['id']/'behavior.sqlite'
 if not original.exists():continue
 with sqlite3.connect(original.resolve().as_uri()+'?mode=ro',uri=True)as old,sqlite3.connect(current.resolve().as_uri()+'?mode=ro',uri=True)as live:
  before=rows(old);after=rows(live)
  for scope,row in before.items():
   if after.get(scope)==row:result['journals']['same']+=1;continue
   a=readwire(old,scope,row);z=readwire(live,scope,after[scope])if scope in after else None
   if a is not None and z is not None and z[:len(a)]==a:result['journals']['appended']+=1
   else:result['journals']['changedNeedsReview']+=1
  result['journals']['new']+=len(set(after)-set(before))
assert result['journals']['changedNeedsReview']==0,'Journal changed across deployment requires review'
with urllib.request.urlopen('http://127.0.0.1:'+str(cfg['environment'].get('PORT','8787'))+'/v1/health',timeout=5)as response:
 health=json.loads(response.read());assert response.status==200 and health.get('ok');result['apiHealth']=True
result['mediaConfigInstalled']=hashlib.sha256((data/'media-resources.json').read_bytes()).hexdigest()=='75bc295c63101164dbd7b85c1161de5aa3d5507d4b8c7fe282c218cc9e6017c2';assert result['mediaConfigInstalled']
result['guardArmed']=(Path(cfg['stateRoot'])/'armed').exists();assert result['guardArmed']
result['backupManifestSha256']=proof['manifestSha256'];result['at']=time.time();(d/'postflight-result.json').write_text(json.dumps(result));print(json.dumps(result))

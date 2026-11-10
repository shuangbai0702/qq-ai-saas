"""Delta edits of existing cognitive products inside the summary transaction.
No new store, history, scheduler or model request. Runtime evidence is immutable.
"""
import hashlib,json,math,re
from memory_scope import valid_scope

SCHEMAS={
 'interest':('companion_interests',('scope','topic'),('scope','topic','weight','hits','updated','evidence','source')),
 'motive':('companion_motives',('scope','subject','topic'),('scope','subject','topic','due','expires','status','evidence')),
 'expression':('social_patterns',('scope','expression'),('scope','expression','situation','sources','weight','updated')),
}
SYSTEM='''existing_memory.products列出既有兴趣、动机与表达条目。可选memory_operations数组只输出有变化的指定条目，每项{"kind":"interest|motive|expression","op":"create|replace|delete","target":{条目主键},"expected_version":"逐字复制version，create为null","value":{创建或替换后的完整认知值，delete省略}}。未指定条目保持，不支持追加或自动拼接。target主键：interest为scope/topic；motive为scope/subject/topic；expression为scope/expression。interest的value为{"weight":0到8}，可降低或放下既有兴趣；新兴趣不伪造互动次数。motive的value为{"status":"open|contacted|resolved|abandoned","due":Unix秒,"expires":Unix秒}，到期与失效不得超过当前时间后7天；新条目只能是模型自己的model_plan，不是已接受的用户约定或平台任务。expression替换value为{"situation":"完整适用语境","weight":0到5}；学习新短语必须继续用learning及真实样本证据，不能凭空create表达。固定persona兴趣、管理员禁用的表达及身份/权限不允许修改。此处只修订认知，不改真实工具、发送或用户约定账本。'''

def version(row):return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
def safe(text,maximum):return isinstance(text,str) and 1<=len(text.strip())<=maximum and not re.search(r'(?i)sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|密钥|密码|系统指令|管理员权限',text)
def read(db,kind,target):
 table,keys,columns=SCHEMAS[kind]
 row=db.execute('SELECT '+','.join(columns)+' FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),tuple(target[k] for k in keys)).fetchone()
 return dict(zip(columns,row)) if row else None

def snapshot(db,scope,people):
 result={}
 for kind,(table,keys,columns) in SCHEMAS.items():
  rows=[]
  for raw in db.execute('SELECT '+','.join(columns)+' FROM '+table+' ORDER BY '+','.join(keys)):
   row=dict(zip(columns,raw));origin=row['scope']
   if not valid_scope(origin) or scope!='bot_shared' and origin not in (scope,'self'):continue
   if kind=='motive' and row['subject'] not in people:continue
   if kind=='interest' and row['source']=='persona':continue
   target={k:row[k] for k in keys}
   # Evidence, sample IDs and observed counts stay in the owned tables. They
   # are not a second raw-history packet or model-editable fields.
   fields={'interest':('weight','source','updated'),'motive':('status','due','expires'),'expression':('situation','weight','updated')}[kind]
   rows.append({'target':target,'version':version(row),'value':{k:row[k] for k in fields}})
  result[kind]=rows
 return result

def apply(db,scope,cfg,data,before,now,audit=None):
 operations=data.get('memory_operations',[]);offered=before.get('memory_state',{}).get('products',{});saved=0;seen=set()
 if not isinstance(operations,list):return 0
 for index,item in enumerate(operations):
  def reject(reason):
   if audit:audit.mark('products',index,'rejected',reason)
  if not isinstance(item,dict) or item.get('kind') not in SCHEMAS or item.get('op') not in ('create','replace','delete'):reject('invalid_operation');continue
  if not cfg.get('selfDevelopment',True):reject('disabled');continue
  kind=item['kind'];op=item['op'];target=item.get('target');table,keys,columns=SCHEMAS[kind]
  if not isinstance(target,dict) or set(target)!=set(keys) or not valid_scope(target.get('scope')):reject('invalid_target');continue
  if any(not safe(target[k],1000) for k in keys):reject('unsafe_target');continue
  token=(kind,tuple(target[k] for k in keys))
  if token in seen:reject('duplicate_target');continue
  prior=next((r for r in offered.get(kind,[]) if r['target']==target),None);current=read(db,kind,target)
  if op=='create':
   if current or prior or item.get('expected_version') is not None or 'expected_version' not in item:reject('target_exists_or_missing_version');continue
   if scope!='bot_shared' and target['scope'] not in (scope,'self'):reject('scope_not_offered');continue
   if kind=='expression':reject('learning_evidence_required');continue
   if kind=='motive' and target['subject'] not in before['memory_state']['subjects']:reject('unknown_subject');continue
  else:
   if not prior or item.get('expected_version')!=prior['version']:reject('target_not_offered_or_stale_version');continue
   if current is None or version(current)!=prior['version']:raise ValueError('semantic_product_snapshot_changed')
  if kind=='interest' and current and current['source']=='persona':reject('fixed_persona');continue
  if kind=='expression' and db.execute('SELECT 1 FROM social_pattern_blocks WHERE scope=? AND expression=?',tuple(target[k] for k in keys)).fetchone():reject('operator_blocked');continue
  value=item.get('value')
  if op!='delete':
   if not isinstance(value,dict):reject('invalid_value');continue
   if kind=='interest':
    weight=value.get('weight')
    if set(value)!={'weight'} or type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=8:reject('invalid_value');continue
   elif kind=='motive':
    if set(value)!={'status','due','expires'} or value['status'] not in ('open','contacted','resolved','abandoned') or any(type(value[k]) not in (int,float) or not math.isfinite(value[k]) for k in ('due','expires')) or not value['due']<=value['expires']<=now+604800:reject('invalid_value');continue
   else:
    weight=value.get('weight')
    if set(value)!={'situation','weight'} or not safe(value['situation'],120) or type(weight) not in (int,float) or not math.isfinite(weight) or not 0<=weight<=5:reject('invalid_value');continue
  where=' AND '.join(k+'=?' for k in keys);args=tuple(target[k] for k in keys)
  if op=='delete':db.execute('DELETE FROM '+table+' WHERE '+where,args)
  elif kind=='interest':
   if current:db.execute('UPDATE companion_interests SET weight=?,updated=? WHERE '+where,(value['weight'],now)+args)
   else:db.execute("INSERT INTO companion_interests VALUES(?,?,?,0,?,'','autonomous_choice')",(target['scope'],target['topic'],value['weight'],now))
  elif kind=='motive':
   if current:db.execute('UPDATE companion_motives SET status=?,due=?,expires=? WHERE '+where,(value['status'],value['due'],value['expires'])+args)
   else:
    from companion_state import motive_record
    db.execute('INSERT INTO companion_motives VALUES(?,?,?,?,?,?,?)',(target['scope'],target['subject'],target['topic'],value['due'],value['expires'],value['status'],motive_record('semantic-memory','model_plan')))
  else:db.execute('UPDATE social_patterns SET situation=?,weight=?,updated=? WHERE '+where,(value['situation'],value['weight'],now)+args)
  seen.add(token);saved+=1
  if audit:audit.mark('products',index,'accepted','applied',True)
 return saved

"""Delta edits of existing cognitive products inside the summary transaction.
No new store, history, scheduler or model request. Runtime evidence is immutable.
"""
import hashlib,json,re
from memory_scope import valid_scope

KINDS=('interest','motive','expression')
def owner(kind):
 if kind=='expression':
  import social
  return social
 import companion_state
 return companion_state

def keys(kind):return ('scope','expression') if kind=='expression' else ('scope','subject','topic') if kind=='motive' else ('scope','topic')

SYSTEM='''existing_memory.products列出既有兴趣、动机与表达条目。可选memory_operations数组只输出有变化的指定条目，每项{"kind":"interest|motive|expression","op":"create|replace|delete","target":{条目主键},"expected_version":"逐字复制version，create为null","value":{创建或替换后的完整认知值，delete省略}}。未指定条目保持，不支持追加或自动拼接。target主键：interest为scope/topic；motive为scope/subject/topic；expression为scope/expression。interest的value为{"weight":0到8}，可降低或放下既有兴趣；新兴趣不伪造互动次数。motive的value为{"status":"open|contacted|resolved|abandoned","due":Unix秒,"expires":Unix秒}，due必须小于等于expires，expires不得超过current_time.unix_seconds后7天；新条目只能是模型自己的model_plan，不是已接受的用户约定或平台任务。expression替换value为{"situation":"完整适用语境，最多120字符","weight":0到5}；学习新短语必须继续用learning及真实样本证据，不能凭空create表达。Persona正文、管理员禁用的表达及身份/权限不允许修改。source为persona的旧派生兴趣是冗余副本，只允许按精确target和version删除，不得创建或改写；删除副本不改变Persona。此处只修订认知，不改真实工具、发送或用户约定账本。'''

from memory_policy import version,safe
def read(db,kind,target):return owner(kind).read_product(db,kind,target)

def snapshot(db,scope,people):
 result={}
 for kind in KINDS:
  fields_key=keys(kind)
  rows=[]
  for row in owner(kind).product_rows(db,kind):
   origin=row['scope']
   legacy_persona=kind=='interest' and origin=='*' and row['source']=='persona'
   if not legacy_persona and (not valid_scope(origin) or scope!='bot_shared' and origin not in (scope,'self')):continue
   if kind=='motive' and row['subject'] not in people:continue
   target={k:row[k] for k in fields_key}
   # Evidence, sample IDs and observed counts stay in the owned tables. They
   # are not a second raw-history packet or model-editable fields.
   fields={'interest':('weight','source','updated'),'motive':('status','due','expires'),'expression':('situation','weight','updated')}[kind]
   rows.append({'target':target,'version':version(row),'value':{k:row[k] for k in fields}})
  result[kind]=rows
 return result

def apply(db,scope,cfg,data,before,now,audit=None,*,source_scopes=()):
 operations=data.get('memory_operations',[]);offered=before.get('memory_state',{}).get('products',{});saved=0;seen=set()
 if not isinstance(operations,list):return 0
 for index,item in enumerate(operations):
  def reject(reason):
   if audit:audit.mark('products',index,'rejected',reason)
  if not isinstance(item,dict) or item.get('kind') not in KINDS or item.get('op') not in ('create','replace','delete'):reject('invalid_operation');continue
  if not cfg.get('selfDevelopment',True):reject('disabled');continue
  kind=item['kind'];op=item['op'];target=item.get('target');fields_key=keys(kind)
  legacy_persona=kind=='interest' and isinstance(target,dict) and target.get('scope')=='*'
  if not isinstance(target,dict) or set(target)!=set(fields_key) or not (valid_scope(target.get('scope')) or legacy_persona):reject('invalid_target');continue
  if any(not safe(target[k],1000) for k in fields_key):reject('unsafe_target');continue
  token=(kind,tuple(target[k] for k in fields_key))
  if token in seen:reject('duplicate_target');continue
  prior=next((r for r in offered.get(kind,[]) if r['target']==target),None);current=read(db,kind,target)
  if op=='create':
   if current or prior or item.get('expected_version') is not None or 'expected_version' not in item:reject('target_exists_or_missing_version');continue
   if target['scope'] not in ({scope,'self'} if scope!='bot_shared' else {'self','bot_shared',*source_scopes}):reject('scope_not_offered');continue
   if kind=='expression':reject('learning_evidence_required');continue
   if kind=='motive' and target['subject'] not in before['memory_state']['subjects']:reject('unknown_subject');continue
  else:
   if not prior or item.get('expected_version')!=prior['version']:reject('target_not_offered_or_stale_version');continue
   if current is None or version(current)!=prior['version']:raise ValueError('semantic_product_snapshot_changed')
  if legacy_persona and (not current or current['source']!='persona' or op!='delete'):reject('legacy_persona_delete_only');continue
  if kind=='interest' and current and current['source']=='persona' and op!='delete':reject('fixed_persona');continue
  if kind=='expression' and owner(kind).product_blocked(db,target):reject('operator_blocked');continue
  value=item.get('value')
  reason=owner(kind).validate_product(kind,op,value,now)
  if reason:reject(reason);continue
  owner(kind).apply_product(db,kind,op,target,value,now)
  seen.add(token);saved+=1
  if audit:audit.mark('products',index,'accepted','applied',True)
 return saved

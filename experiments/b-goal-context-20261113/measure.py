import asyncio,copy,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
import os
os.chdir(ROOT)
OUTPUT=Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'.local/b-goal-context-measurement'
OUTPUT.mkdir(parents=True,exist_ok=True)
from unittest.mock import patch
sys.path.insert(0,str(Path('tests').resolve()))
import activity_main_journal_test as fixture
from activity_goal_delta_test import snapshot
from activity_context import committed_goals,_goal_service
from context_protocol import model_json

def size(value):return len(model_json(value))
async def measure(mode):
 case=fixture.Activity();await case.asyncSetUp();rows=[];truth=snapshot();last=[]
 try:
  for n in range(1,36):
   print(mode,n,flush=True)
   identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'fishbot-B-release-measure-'+str(n)))
   if n==5:truth['goals'][0].update(revision=8,progress='真实进展：已形成初步证据，仍有未完成观察')
   if n==10:truth['goals'].append({'id':'second-exact-goal','revision':1,'title':'保留自主兴趣方向','status':'active','source':{'scope':'self','constraints':['不得自动发送测试消息']}})
   if n==20:truth['goals'][0].update(revision=9,title='最新合法修订：形成反例清单',success_condition='列明反例和缺失证据，不要求自动分享')
   if n==30:truth['goals'][0]['progress']='已有同参数在途分析，等待回执；unknown不可认为完成'
   if n==14:case.tool_step=True
   start=len(case.requests)
   with patch('activity_context.visible_goals',return_value=copy.deepcopy(truth)):
    if mode=='A':
     with patch('activity_context.encode_goal_candidate',lambda *args:None):result=await case.activate(identifier=identifier)
    else:result=await case.activate(identifier=identifier)
   assert result['status']=='completed',(mode,n,result)
   journal=copy.deepcopy(case.st.model_journals['bot_shared'].last.wire)
   assert journal[:len(last)]==last
   if mode=='B':assert committed_goals(journal,'bot_shared')[0]==truth
   service=next(v for msg in reversed(journal) if (v:=_goal_service(msg,'bot_shared')))
   rows.append({'activation':n,'journal_added_chars':size(journal)-size(last),'journal_total_chars':size(journal),'goal_body_chars':size(service['currentGoals']),'request_messages_chars':sum(size(r['messages']) for r in case.requests[start:]),'sdk_requests':len(case.requests)-start})
   if n in (20,30):
    (OUTPUT/(mode+'-real-case-'+str(n)+'.json')).write_text(model_json(case.requests[-1]),encoding='utf-8')
   last=journal
  return {'mode':mode,'unit':'unicode serialized characters; not token billing','rows':rows,'journal_growth35':sum(r['journal_added_chars'] for r in rows),'goal_body35':sum(r['goal_body_chars'] for r in rows),'all_request_messages35':sum(r['request_messages_chars'] for r in rows)}
 finally:await case.asyncTearDown()
async def main():
 mode=sys.argv[1];results=[await measure(mode)]
 (OUTPUT/(mode+'-context-metrics.json')).write_text(json.dumps(results[0],ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps([{k:v for k,v in r.items() if k!='rows'} for r in results],ensure_ascii=False))
asyncio.run(main())

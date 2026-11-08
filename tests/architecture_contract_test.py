"""Public result, real source and transaction contracts without external writes."""
import asyncio,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parents[1]/'references/astrbot')]
import qq_actions
from bot_pipeline import operation_source
from context_protocol import persona_prompt,PERSONA_PREFIX,PERSONA_IMAGES
class Contracts(unittest.IsolatedAsyncioTestCase):
 async def test_real_tool_preflight_rejections_do_not_become_completed_reads(self):
  import group_actions
  from tool_outcome import structured
  db=sqlite3.connect(':memory:')
  event=NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda k,d=None:d,get_message_str=lambda:'查看QQ空间')
  cfg={'behavior':{'ownerUin':'12345','qqProfile':True,'qzone':True}}
  try:
   tools=qq_actions.tools(event,cfg,db,lambda:True)+group_actions.tools(event,cfg,db,lambda:True,lambda *args:None)
   self.assertTrue(tools)
   for tool in tools:
    value=json.loads(await structured(tool).call(NS(context=NS(event=object()))))
    self.assertEqual(value['execution'],'failed',tool.name)
    self.assertEqual(value['receipt']['status'],'rejected',tool.name)
  finally:db.close()
 def test_loaded_attestation_detects_a_replaced_function(self):
  import model_cache
  before=model_cache.loaded_build();self.assertTrue(before);self.assertTrue(all(row['sourceMatchesLoaded'] for row in before.values()))
  original=model_cache.begin_attempt
  try:
   model_cache.begin_attempt=lambda *args:None
   self.assertFalse(model_cache.loaded_build()['model_cache']['sourceMatchesLoaded'])
  finally:model_cache.begin_attempt=original
 def event(self,group='',author='12345',identifier='shared'):
  return NS(get_group_id=lambda:group,get_sender_id=lambda:author,get_self_id=lambda:'99999',get_extra=lambda k,d=None:d,get_message_str=lambda:'发条说说：你好',message_obj=NS(message_id=identifier))
 def test_source_key_is_unambiguous_and_legacy_unknown_remains_a_fence(self):
  db=sqlite3.connect(':memory:');qq_actions.initialize(db);a=self.event();b=self.event('12345');c=self.event('',author='23456')
  self.assertEqual(len({operation_source(e) for e in [a,b,c]}),3)
  db.execute('INSERT INTO qq_action_attempts VALUES(?,?,?,?)',('shared','publish',time.time(),'attempted'))
  self.assertEqual(qq_actions.attempt(db,a,'publish')[0],'attempted')
  db.close()
 def test_reading_limits_does_not_commit_an_unrelated_transaction(self):
  db=sqlite3.connect(':memory:');qq_actions.initialize(db);db.execute('CREATE TABLE unrelated(value TEXT)');db.commit();db.execute("INSERT INTO unrelated VALUES('uncommitted')")
  qq_actions.limit_context(db,{});db.rollback();self.assertEqual(db.execute('SELECT count(*) FROM unrelated').fetchone()[0],0);db.close()
 async def test_protocol_confirmed_survives_local_commit_failure_without_resend(self):
  db=sqlite3.connect(':memory:');qq_actions.initialize(db);calls=[];event=self.event()
  async def login():return{'user_id':99999}
  async def publish(**kw):calls.append(kw)
  event.bot=NS(get_login_info=login,send_qzone_msg=publish)
  class FailingCommit:
   def __getattr__(self,key):return getattr(db,key)
   def execute(self,sql,*args):
    if sql.startswith('UPDATE qq_action_attempts SET status='):raise sqlite3.OperationalError('injected local failure after ACK')
    return db.execute(sql,*args)
  cfg={'behavior':{'ownerUin':'12345','qzone':True}}
  result=json.loads(await qq_actions.execute(event,cfg,FailingCommit(),'publish','真实内容',lambda:True))
  self.assertEqual(result['receipt']['status'],'confirmed');self.assertFalse(result['state_committed']);self.assertEqual(len(calls),1)
  await qq_actions.execute(event,cfg,db,'publish','真实内容',lambda:True);self.assertEqual(len(calls),1);db.close()
 def test_persona_prefix_once_no_data_task_side_effect(self):
  value=persona_prompt('原角色',images=True);self.assertEqual(persona_prompt(value,images=True),value)
  self.assertEqual(value.count(PERSONA_PREFIX),1);self.assertEqual(value.count(PERSONA_IMAGES),1)
  self.assertNotIn(PERSONA_IMAGES,persona_prompt('原角色'))
if __name__=='__main__':unittest.main()

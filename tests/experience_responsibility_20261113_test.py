"""Automatic responsibility is separate from explicit durable history recall."""
import copy,json,os,sqlite3,sys,tempfile,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from aiohttp import web
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
import execution_memory
from bot_pipeline import experience_context
from context_foundation import read

class CurrentResponsibility(unittest.TestCase):
 def test_completed_history_excluded_pending_and_scope_isolation_preserved(self):
  with closing(sqlite3.connect(':memory:')) as db:
   db.execute('CREATE TABLE pipeline_actions(id TEXT,at REAL,state TEXT,result TEXT)')
   rows=[{'id':status,'sourceScope':'private:12345678','kind':'research','status':status,'objective':status} for status in ('completed','planned','executing','partial','unknown','uncertain')]
   rows.append({'id':'other','sourceScope':'group:55555','kind':'research','status':'planned','objective':'other scope'})
   rows.append({'id':'unfinished','sourceScope':'private:12345678','kind':'research','status':'failed','unfinished':['needed']})
   decisions=[{'cycleId':r['id'],'sourceScope':r['sourceScope'],'action':{'id':r['id'],'objective':r.get('objective')}} for r in rows]
   history={'actions':rows,'decisions':decisions};before=copy.deepcopy(history);store=NS(db=db,autonomous_experience=history)
   view=experience_context(store,'private:12345678',{'ownerUin':'12345678'})
   expected={'planned','executing','partial','unknown','uncertain','unfinished'}
   self.assertEqual({a['id'] for a in view['actions']},expected)
   self.assertEqual({d['action']['id'] for d in view['decisions']},expected)
   self.assertEqual([a['id'] for a in experience_context(store,'group:55555',{})['actions']],['other'])
   self.assertEqual(history,before)

 def test_fresh_foundation_has_no_execution_or_completed_history(self):
  db=NS(execute=lambda *args:[])
  store=NS(db=db,model_journals={},reflection=NS(core_note=lambda *args,**kwargs:'remembered preference'))
  with patch('memory_scope.fact_rows',return_value=[]):
   foundation=read(store,{'memorySummary':False},'private:12345678','12345678')
  self.assertEqual(foundation['core_note'],'remembered preference')
  self.assertNotIn('completed_experience',foundation);self.assertNotIn('execution_experience',foundation)

class ExplicitRecall(unittest.IsolatedAsyncioTestCase):
 async def test_actual_http_explicit_tool_return_is_complete_and_projection_is_separate(self):
  folder=self.enterContext(tempfile.TemporaryDirectory());self.enterContext(patch.dict(os.environ,{'ASTRBOT_ROOT':folder}))
  full={'sampledAt':123,'experiences':[{'actionId':status,'execution':'completed','receipt':{'status':status},'summary':'full history '*500,'future':{'untouched':[None,True]}} for status in ('confirmed','not_applicable','unknown')]}
  received=[]
  async def handler(request):
   received.append(await request.json());return web.json_response(full)
  app=web.Application();app.router.add_post('/internal/v1/bots/fixture/memory/experience',handler)
  runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
  bridge={'url':'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1]),'botId':'fixture','token':'fixture'}
  try:
   import runtime_bootstrap
   runtime_bootstrap.initialize_runtime_bootstrap()
   from slim_bootstrap import install
   install()
   with patch('execution_memory.manifest',return_value=bridge):
    tool=execution_memory.tools({'config':{}},NS(get_sender_id=lambda:'12345678'),'private:12345678',lambda:True)[0]
    recalled=json.loads(await tool.call(None,query='confirmed',limit=8))
   self.assertEqual(recalled,full);self.assertEqual(received[0]['scope'],'private:12345678');self.assertEqual(received[0]['query'],'confirmed')
   foundation={};projected=execution_memory.context_projection(recalled,foundation)
   self.assertEqual(projected,{'experiences':[full['experiences'][2]]});self.assertEqual(foundation,{})
   self.assertEqual(recalled,full)
  finally:await runner.cleanup()

if __name__=='__main__':unittest.main()

"""Real AstrBot SDK request assembled by the admin probe; isolated HTTP boundary."""
import asyncio,json,os,sys,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
async def main():
 with tempfile.TemporaryDirectory() as root:
  os.environ['ASTRBOT_ROOT']=root
  import runtime_bootstrap
  runtime_bootstrap.initialize_runtime_bootstrap()
  from slim_bootstrap import install
  install()
  import httpx
  from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
  from astrbot.core.platform.platform_metadata import PlatformMetadata
  from behavior import install_runtime
  from model_probe_admin import request
  from shared_context import JOURNAL_SCOPE
  bodies=[]
  async def handler(req):
   bodies.append(json.loads(req.content))
   return httpx.Response(200,json={'id':'probe-sdk','model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'调试链路可用'},'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':8,'total_tokens':108}})
  with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda p,c:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
   provider=ProviderOpenAIOfficial({'id':'provider-probe','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'},{})
  lease={'root':root,'tenantId':'tenant','botId':'probe','generation':1,'revision':1,'expectedUin':'90000000','config':{'systemPrompt':'人物设定\n[引用习惯]私聊非必要不引用','behavior':{'memory':True}}}
  platform=NS(meta=lambda:PlatformMetadata('aiocqhttp','fixture','probe'),bot=NS())
  core=NS(event_bus=NS(stopped_platforms=set()),provider_manager=NS(inst_map={'provider-probe':provider}),platform_manager=NS(get_insts=lambda:[platform]),conversation_manager=None)
  runtime=install_runtime(core,{'probe':lease});runtime['manage']('probe',{'action':'list'});st=runtime['stores']['probe']
  import shared_context
  shared_context.initialize(st.db);st.shared_context=True;st.reflection.shared_context=True;st.db.commit()
  try:
   before=st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]
   result=await request(core._hosted_autonomous,'probe',st,lease,{'requestId':'11111111-1111-4111-8111-111111111111','scope':'private:12345678'})
   assert len(bodies)==1 and not bodies[0].get('tools')
   assert bodies[0].get('max_tokens',bodies[0].get('max_completion_tokens'))<=1024
   assert result['quoteRuleInSystem'] and result['journalCommitted'] and not result['qqSend']
   assert st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]==before
   wire=runtime['stores']['probe'].model_journals[JOURNAL_SCOPE].last.wire
   assert '[管理员调试' in json.dumps(wire,ensure_ascii=False)
   assert any(m.get('role')=='system' and '[引用习惯]' in str(m.get('content')) for m in bodies[0]['messages'])
   assert not st.active
   print('PASS real SDK admin probe: one HTTP, System quote rule, canonical Journal, no tools/QQ/business round')
  finally:
   runtime['close']();await provider.client.close()
asyncio.run(main())


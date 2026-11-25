"""Owner media follow-ups use the same Qzone authority and receipt boundary."""
import asyncio,io,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch,AsyncMock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.message.components import Image,Reply
import qq_actions,qq_publish_media,quote_reply


class QzoneOwnerMedia(unittest.IsolatedAsyncioTestCase):
 def fixture(self,protocol='napcat'):
  folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  extras={};live=[True];calls=[]
  from PIL import Image as PILImage
  output=io.BytesIO();PILImage.new('RGB',(3,2),'red').save(output,'PNG');raw=output.getvalue()
  async def login():return {'user_id':99999}
  async def publish(**kw):
   calls.append({**kw,'bytes':[Path(file).read_bytes() for file in kw.get('images',[])]});return {'tid':'fixture'}
  components=[Image(file='fixture.png',url='https://qpic.cn/fixture')]
  event=NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda k,d=None:extras.get(k,d),set_extra=lambda k,v:extras.update({k:v}),get_message_str=lambda:'发布空间',get_messages=lambda:components,message_obj=NS(message_id='100',raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture','file':'fixture.png'}}]}),bot=NS(get_login_info=login,send_qzone_msg=publish))
  settings={'root':folder.name,'botId':'test','generation':1,'revision':1,'protocol':protocol}
  cfg={'behavior':{'ownerUin':'12345','qzone':True}}
  tool=next(t for t in qq_actions.tools(event,cfg,db,lambda:live[0],settings) if t.name=='hosted_qq_publish')
  return NS(db=db,event=event,extras=extras,live=live,calls=calls,raw=raw,settings=settings,tool=tool,context=NS(context=NS(event=event)))

 async def test_original_attachments_share_payload_and_cleanup_for_both_backends(self):
  for protocol in ('napcat','llbot'):
   f=self.fixture(protocol)
   with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)):
    result=json.loads(await f.tool.call(f.context,content='附原图',image_sources=['current']))
   self.assertEqual(result['receipt']['status'],'confirmed');self.assertEqual(len(f.calls),1)
   call=f.calls[0];self.assertEqual(call['bytes'],[f.raw]);self.assertEqual(call['ugc_right'],4)
   self.assertTrue(all(not Path(file).exists() for file in call['images']))
   self.assertNotIn(f.settings['root'],json.dumps(result));self.assertEqual(list(Path(f.settings['root']).iterdir()),[])
   self.assertEqual(f.tool.parameters['properties']['image_sources']['maxItems'],9)
   await f.tool.call(f.context,content='重复',image_sources=['current']);self.assertEqual(len(f.calls),1)

 async def test_quote_and_scoped_followup_sources_reuse_real_message_verification(self):
  f=self.fixture();image=f.event.get_messages()[0]
  image.url=image.file='/SDK/model-copy.png'
  f.event.get_messages=lambda:[Reply(id='99',sender_id='12345',chain=[image])]
  f.event.message_obj.raw_message={'message':[{'type':'reply','data':{'id':'99'}}]}
  f.event.bot.get_msg=AsyncMock(return_value={'message_id':99,'sender':{'user_id':12345},'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture'}}]})
  with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)) as download:
   await f.tool.call(f.context,content='引用原图',image_sources=['current'])
   download.assert_awaited_once_with('https://qpic.cn/fixture',max_bytes=qq_publish_media.MAX_IMAGE_BYTES)
  self.assertEqual(f.calls[0]['bytes'],[f.raw])
  f.event.message_obj=NS(message_id='101',raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture'}}]})
  quote_reply.observe(f.db,f.event,f.settings,'private:12345');f.event.get_messages=lambda:[]
  async def get_msg(**kw):return {'message_id':101,'sender':{'user_id':12345},'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture'}}]}
  f.event.bot.get_msg=get_msg;f.event.message_obj.message_id='102';f.event.message_obj.raw_message={'message':[]}
  with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)):
   result=json.loads(await f.tool.call(f.context,content='刚才原图',image_sources=['current']))
  self.assertEqual(result['receipt']['status'],'confirmed');self.assertEqual(len(f.calls),2)
  f.event.message_obj.message_id='103';f.settings['generation']=2
  with patch.object(qq_publish_media,'download',AsyncMock()) as download:
   result=json.loads(await f.tool.call(f.context,content='过期图',image_sources=['current']))
   self.assertEqual(result['receipt']['status'],'rejected');download.assert_not_awaited()
  self.assertEqual(len(f.calls),2)

 async def test_invalid_or_failed_attachments_never_fall_back_to_text(self):
  f=self.fixture()
  for sources in (None,'https://qpic.cn/a',False,{},['current','current'],['qm_'+'a'*20],['file:///private'],['current']*10):
   result=json.loads(await f.tool.call(f.context,content='不能降级',image_sources=sources))
   self.assertEqual(result['receipt']['status'],'rejected',sources)
  for payload in (b'not an image',b'x'*(qq_publish_media.MAX_IMAGE_BYTES+1)):
   with patch.object(qq_publish_media,'download',AsyncMock(return_value=payload)):
    self.assertEqual(json.loads(await f.tool.call(f.context,content='坏图',image_sources=['current']))['receipt']['status'],'rejected')
  self.assertEqual(f.calls,[]);self.assertEqual(f.db.execute('SELECT count(*) FROM qq_action_attempts').fetchone()[0],0)

 async def test_normal_photo_is_not_subject_to_sticker_thumbnail_size_limit(self):
  import random
  from PIL import Image as PILImage
  f=self.fixture();output=io.BytesIO()
  PILImage.frombytes('RGB',(600,600),random.Random(1).randbytes(600*600*3)).save(output,'JPEG',quality=95)
  original=output.getvalue()
  with patch.object(qq_publish_media,'download',AsyncMock(return_value=original)):
   result=json.loads(await f.tool.call(f.context,content='原照片',image_sources=['current']))
  self.assertEqual(result['receipt']['status'],'confirmed');self.assertEqual(f.calls[0]['bytes'],[original])

 async def test_preparation_race_rechecks_generation_and_duplicate_operation(self):
  for stale in (False,True):
   f=self.fixture();entered=asyncio.Event();resume=asyncio.Event();count=[0]
   async def download(*a,**kw):
    count[0]+=1
    if count[0]==1:entered.set();await resume.wait()
    return f.raw
   with patch.object(qq_publish_media,'download',download):
    pending=asyncio.create_task(f.tool.call(f.context,content='图',image_sources=['current']));await entered.wait()
    if stale:f.live[0]=False
    else:await f.tool.call(f.context,content='图',image_sources=['current'])
    resume.set();await pending
   self.assertEqual(len(f.calls),0 if stale else 1);self.assertEqual(list(Path(f.settings['root']).iterdir()),[])

 async def test_multiple_images_keep_selection_order_and_failed_preparation_drains_children(self):
  from PIL import Image as PILImage
  f=self.fixture();out=io.BytesIO();PILImage.new('RGB',(2,3),'blue').save(out,'PNG');second=out.getvalue()
  f.event.get_messages=lambda:[Image(file='a',url='https://qpic.cn/a'),Image(file='b',url='https://qpic.cn/b')]
  f.event.message_obj.raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/'+s}}for s in ('a','b')]}
  release=asyncio.Event()
  async def download(url,**kw):
   if url.endswith('/a'):await release.wait();return f.raw
   release.set();return second
  with patch.object(qq_publish_media,'download',download):await f.tool.call(f.context,content='两图',image_sources=['current'])
  self.assertEqual(f.calls[0]['bytes'],[f.raw,second])
  f=self.fixture();f.event.get_messages=lambda:[Image(file='a',url='https://qpic.cn/a'),Image(file='b',url='https://qpic.cn/b')]
  f.event.message_obj.raw_message={'message':[{'type':'image','data':{'url':'https://qpic.cn/'+s}}for s in ('a','b')]}
  entered=asyncio.Event();drained=asyncio.Event()
  async def failing(url,**kw):
   if url.endswith('/a'):
    entered.set()
    try:await asyncio.Future()
    finally:drained.set()
   await entered.wait();raise ValueError('failed source')
  with patch.object(qq_publish_media,'download',failing):
   result=json.loads(await f.tool.call(f.context,content='不发残缺图文',image_sources=['current']))
  self.assertEqual(result['receipt']['status'],'rejected');self.assertTrue(drained.is_set());self.assertEqual(f.calls,[])

 async def test_unknown_or_cancelled_dispatch_cleans_files_and_never_resends(self):
  for cancel in (False,True):
   f=self.fixture();entered=asyncio.Event();paths=[]
   async def publish(**kw):
    paths.extend(kw['images']);self.assertTrue(all(Path(p).is_file() for p in paths));entered.set()
    if cancel:await asyncio.Future()
    raise TimeoutError('unknown')
   f.event.bot.send_qzone_msg=publish
   with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)):
    pending=asyncio.create_task(f.tool.call(f.context,content='图',image_sources=['current']));await entered.wait()
    if cancel:
     pending.cancel()
     with self.assertRaises(asyncio.CancelledError):await pending
    else:self.assertEqual(json.loads(await pending)['receipt']['status'],'unknown')
    self.assertEqual(json.loads(await f.tool.call(f.context,content='再次',image_sources=['current']))['receipt']['status'],'unknown')
   self.assertTrue(all(not Path(p).exists() for p in paths));self.assertEqual(len(paths),1)

 async def test_protocol_failure_receipts_distinguish_not_published_and_unknown(self):
  from aiocqhttp.exceptions import ActionFailed
  for stage,status in [('prepare','rejected'),('upload','rejected'),('publish','rejected'),('publish','unknown')]:
   f=self.fixture('llbot')
   f.event.bot.send_qzone_msg=AsyncMock(side_effect=ActionFailed({'retcode':1200,'data':{'stage':stage,'code':'qzone_'+stage+'_failed','receipt':{'status':status}},'message':'secret-cookie'}))
   with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)),patch.object(qq_actions.asyncio,'wait_for',wraps=asyncio.wait_for) as wait:
    result=json.loads(await f.tool.call(f.context,content='fixture',image_sources=['current']))
   self.assertEqual(result['receipt']['status'],status)
   self.assertEqual(f.db.execute('SELECT status FROM qq_action_attempts').fetchone()[0],'not_sent' if status=='rejected' else 'attempted')
   self.assertIn(qq_actions.QQ_PUBLISH_TIMEOUT_SECONDS,[c.args[1] for c in wait.call_args_list if len(c.args)>1])
   self.assertNotIn('secret-cookie',json.dumps(result));self.assertEqual(list(Path(f.settings['root']).iterdir()),[])
   await f.tool.call(f.context,content='不能重发')
   self.assertEqual(f.event.bot.send_qzone_msg.await_count,1)

 async def test_sdk_http_wire_exposes_one_attachment_parameter_and_consumes_real_tool_receipt(self):
  import os,httpx
  f=self.fixture();requests=[]
  import runtime_bootstrap
  with patch.dict(os.environ,{'ASTRBOT_ROOT':f.settings['root']}):runtime_bootstrap.initialize_runtime_bootstrap()
  # Exercise the real SDK stage that rewrites both component URL and file.
  # Its reduced/temporary recognition copy must never become a publish source.
  from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage
  local=Path(f.settings['root'])/'recognition.png';local.write_bytes(f.raw)
  f.event.track_temporary_local_file=lambda path:None;f.event.untrack_temporary_local_file=lambda path:None
  with patch.object(Image,'convert_to_file_path',AsyncMock(return_value=str(local))):
   await PreProcessStage()._normalize_image_component(f.event,f.event.get_messages()[0])
  self.assertEqual(f.event.get_messages()[0].url,str(local))
  from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
  from astrbot.core.provider.entities import ProviderRequest
  from astrbot.core.agent.tool import ToolSet
  from astrbot.core.agent.hooks import BaseAgentRunHooks
  from astrbot.core.agent.run_context import ContextWrapper
  from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
  from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
  from mcp.types import CallToolResult,TextContent
  async def handler(request):
   self.assertEqual(request.url.host,'fixture.invalid');requests.append(json.loads(request.content))
   message={'role':'assistant','content':'已确认'};finish='stop'
   if len(requests)==1:
    message={'role':'assistant','content':None,'tool_calls':[{'id':'publish-image','type':'function','function':{'name':'hosted_qq_publish','arguments':json.dumps({'content':'附图说说','image_sources':['current']})}}]};finish='tool_calls'
   return httpx.Response(200,json={'id':'fixture','model':'fixture','choices':[{'index':0,'message':message,'finish_reason':finish}]})
  class Executor(BaseFunctionToolExecutor):
   @classmethod
   async def execute(cls,tool,run_context,**arguments):
    yield CallToolResult(content=[TextContent(type='text',text=await tool.call(run_context,**arguments))])
  with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda *a:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
   provider=ProviderOpenAIOfficial({'id':'qq-wire-fixture','type':'openai_chat_completion','model':'fixture','key':['fixture'],'api_base':'https://fixture.invalid/v1'},{})
  try:
   runner=ToolLoopAgentRunner()
   await runner.reset(provider=provider,request=ProviderRequest(prompt='附这张图发说说',system_prompt='fixture system',func_tool=ToolSet(tools=[f.tool])),run_context=ContextWrapper(context=f.context.context),tool_executor=Executor(),agent_hooks=BaseAgentRunHooks(),request_max_retries=1)
   with patch.object(qq_publish_media,'download',AsyncMock(return_value=f.raw)) as download:
    async for _ in runner.step_until_done(max_step=3):pass
    download.assert_awaited_once_with('https://qpic.cn/fixture',max_bytes=qq_publish_media.MAX_IMAGE_BYTES)
   self.assertTrue(runner.done());self.assertEqual(len(requests),2);self.assertEqual(len(f.calls),1)
   self.assertEqual(len(requests[0]['tools']),1)
   self.assertIn('image_sources',requests[0]['tools'][0]['function']['parameters']['properties'])
   self.assertEqual(requests[0]['tools'],requests[1]['tools'])
   self.assertEqual(requests[1]['messages'][:len(requests[0]['messages'])],requests[0]['messages'])
   receipt=next(m for m in requests[1]['messages'] if m['role']=='tool')
   self.assertEqual(json.loads(receipt['content'])['receipt']['status'],'confirmed')
   self.assertNotIn(f.settings['root'],json.dumps(requests));self.assertNotIn('base64',json.dumps(requests))
  finally:await provider.client.close()

 async def test_reply_identity_is_rechecked_and_local_components_are_not_authority(self):
  for changed in ('message_id','sender','group_id'):
   f=self.fixture();f.event.message_obj.raw_message={'message':[{'type':'reply','data':{'id':'99'}}]}
   f.event.get_messages=lambda:[Reply(id='99',sender_id='12345',chain=[Image(file='/private/model-copy.png')])]
   response={'message_id':99,'sender':{'user_id':12345},'message':[{'type':'image','data':{'url':'https://qpic.cn/fixture'}}]}
   response[changed]={'user_id':67890} if changed=='sender' else '98765'
   f.event.bot.get_msg=AsyncMock(return_value=response)
   with patch.object(qq_publish_media,'download',AsyncMock()) as download:
    result=json.loads(await f.tool.call(f.context,content='引用图',image_sources=['current']))
    self.assertEqual(result['receipt']['status'],'rejected');download.assert_not_awaited()
   self.assertEqual(f.calls,[])
  f=self.fixture();f.event.message_obj.raw_message={'message':[]}
  with patch.object(qq_publish_media,'download',AsyncMock()) as download:
   result=json.loads(await f.tool.call(f.context,content='没有真实来源',image_sources=['current']))
   self.assertEqual(result['receipt']['status'],'rejected');download.assert_not_awaited()

 async def test_image_followup_keeps_capability_and_receipt_fences(self):
  db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  calls=[];extras={};live=[True]
  async def identity():return {'user_id':99999}
  async def publish(**kw):calls.append(kw);return {'tid':'fixture'}
  event=NS(get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',get_extra=lambda k,d=None:extras.get(k,d),get_message_str=lambda:'',get_messages=lambda:[Image(file='https://qpic.cn/fixture',url='https://qpic.cn/fixture')],message_obj=NS(message_id='image-followup'),bot=NS(get_login_info=identity,send_qzone_msg=publish))
  cfg={'behavior':{'ownerUin':'12345','qzone':True}}
  context=NS(context=NS(event=event))
  tool=next(t for t in qq_actions.tools(event,cfg,db,lambda:live[0]) if t.name=='hosted_qq_publish')
  result=json.loads(await tool.call(context,content='刚才确认的说说正文'))
  self.assertEqual(result['receipt']['status'],'confirmed');self.assertEqual(len(calls),1)
  await tool.call(context,content='刚才确认的说说正文');self.assertEqual(len(calls),1)
  event.message_obj.message_id='other';event.get_sender_id=lambda:'67890'
  self.assertEqual(json.loads(await tool.call(context,content='无权发送'))['receipt']['status'],'rejected')
  event.get_sender_id=lambda:'12345';cfg['behavior']['qzone']=False
  self.assertEqual(json.loads(await tool.call(context,content='已停用'))['receipt']['status'],'rejected')
  cfg['behavior']['qzone']=True
  for flag in ('_hosted_tool_completion','_hosted_initiative'):
   extras[flag]=True
   self.assertEqual(json.loads(await tool.call(context,content='不是主人新请求'))['receipt']['status'],'rejected');extras.clear()
  live[0]=False
  self.assertEqual(json.loads(await tool.call(context,content='失效请求'))['receipt']['status'],'rejected')
  self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()

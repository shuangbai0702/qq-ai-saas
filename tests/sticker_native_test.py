"""Actual AstrBot SDK events and send wrapper; fixture download/transport only."""
import asyncio,base64,io,json,os,sys,tempfile
from unittest.mock import AsyncMock,patch
from pathlib import Path
from types import SimpleNamespace
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
tmp=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=tmp.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from aiocqhttp import Event
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
from astrbot.core.platform.astrbot_message import AstrBotMessage,MessageMember,Group
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.message_type import MessageType
from astrbot.core.message.components import Plain,Image,Face,At,Reply
from astrbot.api.event import MessageChain
import sticker_collection
from behavior import install_runtime

async def run():
 sent=[];payloads=[];after_send=None
 async def transport(self,message):
  sent.append((self.get_platform_id(),message));payloads.append(await AiocqhttpMessageEvent._parse_onebot_json(message))
  if after_send:after_send()
 async def no_model(self,event):raise AssertionError('unmentioned collection must not call model')
 AiocqhttpMessageEvent.send=transport;PipelineScheduler.execute=no_model
 async def fetch(url):return (root/'templates/reactions/laugh.png').read_bytes()
 sticker_collection._download=fetch
 settings={name:{'botId':name,'generation':1,'root':str(Path(tmp.name)/name),'config':{'trigger':'mention','behavior':{'qqFaces':False,'naturalConversation':False,'followupSeconds':0,'stickerCollect':True,'stickerGroups':['12345'],'memeEnabled':True,'memeProbability':1,'galleryEnabled':True,'dailyMemeLimit':20,'memeCooldownSeconds':20}}} for name in ['a','b']}
 runtime=install_runtime(SimpleNamespace(event_bus=SimpleNamespace(stopped_platforms=set())),settings)
 # Run the pinned native OneBot converter, including nested quoted messages.
 # URL normalization must preserve segment order, identity and raw evidence.
 adapter=object.__new__(AiocqhttpAdapter);adapter.bot=SimpleNamespace(call_action=AsyncMock())
 source={'post_type':'message','message_type':'private','sub_type':'friend','self_id':90000,'user_id':123456,'message_id':321,'sender':{'user_id':123456,'nickname':'fixture'},'message':[{'type':'text','data':{'text':'前'}},{'type':'mface','data':{'url':'https://gxh.vip.qq.com/club/item/fixture.gif','emoji_id':'123'}},{'type':'text','data':{'text':'后'}}]}
 raw=Event.from_payload(source);converted=await adapter._convert_handle_message_event(raw)
 assert isinstance(converted.message[1],Image) and converted.message[1].file=='https://gxh.vip.qq.com/club/item/fixture.gif'
 assert converted.message_str=='前后' and converted.raw_message is raw and raw.message[1]['type']=='mface'
 assert converted.self_id=='90000' and converted.sender.user_id=='123456'
 from incoming_media import normalize
 for url in ['http://127.0.0.1/a','file:///etc/passwd','https://gxh.vip.qq.com.evil.invalid/a','https://x@qpic.cn/a',None]:
  broken=Event.from_payload({**source,'message':[{'type':'mface','data':{'url':url}}]})
  parsed=await adapter._convert_handle_message_event(broken)
  assert not any(isinstance(c,Image) for c in parsed.message) and '不可用' in parsed.message_str
 direct=Event.from_payload({**source,'message':[{'type':'image','data':{'url':'https://multimedia.nt.qq.com.cn/fixture','sub_type':1}}]})
 parsed=await adapter._convert_handle_message_event(direct)
 assert isinstance(parsed.message[0],Image) and parsed.message[0].file==direct.message[0]['data']['url'] and 'file' not in direct.message[0]['data']
 adapter.bot.call_action.return_value={**source,'message_id':320}
 quoted=Event.from_payload({**source,'message':[{'type':'reply','data':{'id':'320'}}]})
 parsed=await adapter._convert_handle_message_event(quoted)
 assert isinstance(parsed.message[0],Reply) and any(isinstance(c,Image) for c in parsed.message[0].chain)
 print('PASS incoming custom mface and url-only images, native quoted image conversion, raw identity and invalid URL boundaries')
 def event(id,group='12345',text='群友发表情'):
  raw=Event.from_payload({'post_type':'message','message_type':'group','sub_type':'normal','self_id':90000,'user_id':123456,'group_id':int(group),'message_id':123,'message':[{'type':'image','data':{'sub_type':1,'url':'https://gchat.qpic.cn/fixture'}}]})
  msg=AstrBotMessage();msg.type=MessageType.GROUP_MESSAGE;msg.self_id='90000';msg.session_id=group;msg.message_id='123';msg.group=Group(group_id=group);msg.sender=MessageMember('123456','群友');msg.message=[Plain(text)];msg.message_str=text;msg.raw_message=raw
  e=AiocqhttpMessageEvent(text,msg,PlatformMetadata('aiocqhttp','fixture',id),group,bot=None);e.set_extra('_hosted_lease',settings[id]);e.set_extra('_hosted_generation',1);return e
 try:
  await AiocqhttpMessageEvent.send(event('a',text='你好'),MessageChain(chain=[Plain('正常文字[QQ_FACE:14]'),Face(id=4)]))
  assert ''.join(x.text for x in sent[-1][1].chain if isinstance(x,Plain))=='正常文字'
  assert not any(isinstance(x,Face) for x in sent[-1][1].chain),'disabled QQ faces leaked into an ordinary reply'
  e=event('a');await PipelineScheduler.execute(None,e);await runtime['stores']['a'].social.stickers.task
  items=runtime['manage']('a',{'action':'list_stickers'})['items'];assert len(items)==1 and items[0]['status']=='pending'
  assert runtime['manage']('b',{'action':'list_stickers'})['items']==[]
  item=items[0];runtime['manage']('a',{'action':'review_sticker','id':item['id'],'status':'approved','meaning':'轻松玩笑','expectedVersion':1})
  await AiocqhttpMessageEvent.send(e,MessageChain(chain=[Plain('哈哈[MEME:'+item['id']+']')]))
  assert any(isinstance(x,Image) for x in sent[-1][1].chain)
  for ev in [event('a','54321'),event('b')]:
   await AiocqhttpMessageEvent.send(ev,MessageChain(chain=[Plain('回复[MEME:'+item['id']+']')]))
   assert not any(isinstance(x,Image) for x in sent[-1][1].chain)
  gallery=Path(settings['a']['root'])/'gallery';gallery.mkdir();(gallery/'gal_test.png').write_bytes((root/'templates/reactions/laugh.png').read_bytes());(gallery/'gal_test.json').write_text(json.dumps({'id':'gal_test','meaning':'角色照片'}))
  await AiocqhttpMessageEvent.send(event('a',text='发张图片'),MessageChain(chain=[Plain('[IMAGE:gal_test]')]))
  assert any(isinstance(x,Image) for x in sent[-1][1].chain)
  assert all('[IMAGE:' not in ''.join(x.text for x in message.chain if isinstance(x,Plain)) for _,message in sent)
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('a',text='发个表情包'),MessageChain(chain=[Plain('[QQ_FACE:14]'),Face(id=4)]))
  assert len(sent)==before,'QQ faces must not masquerade as an image sticker'
  await AiocqhttpMessageEvent.send(event('a',text='发个表情包'),MessageChain(chain=[Plain('没有合适的表情包[QQ_FACE:14]')]))
  assert not any(isinstance(x,Face) for x in sent[-1][1].chain)
  # Real installed animated pack through AstrBot's SDK, not a placeholder icon.
  pack=json.loads((root/'templates/meme-packs/blue-fat-fish/index.json').read_text(encoding='utf8'))
  settings['b']['config']['behavior'].update({'memePacks':['blue-fat-fish'],'memeProbability':0})
  requested=event('b',text='发个表情包');await AiocqhttpMessageEvent.send(requested,MessageChain(chain=[Plain('[MEME:'+pack['items'][0]['id']+']')]))
  assert sent[-1][0]=='b' and any(isinstance(x,Image) for x in sent[-1][1].chain)
  assert all('[MEME:' not in ''.join(x.text for x in msg.chain if isinstance(x,Plain)) for _,msg in sent)
  # Whitespace-wrapped control words/IDs and adjacent SDK text chunks still
  # select the same offered/authorized image and never leak protocol text.
  item=pack['items'][0];mid=len(item['id'])//2;wrapped='[MEM\nE:'+item['id'][:mid]+'\n'+item['id'][mid:]+']'
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44460',text='正常聊天'),MessageChain(chain=[Plain(wrapped[:6]),Plain(wrapped[6:]+'回应')]))
  assert len(sent)==before+1 and any(isinstance(c,Image) for c in sent[-1][1].chain)
  assert ''.join(c.text for c in sent[-1][1].chain if isinstance(c,Plain))=='回应'
  favorites=Path(settings['b']['root'])/'qq-favorites';(favorites/'images').mkdir(parents=True)
  fid='qqf_0123456789abcdef';(favorites/'images'/(fid+'.gif')).write_bytes((root/'templates/meme-packs/blue-fat-fish'/pack['items'][0]['file']).read_bytes())
  (favorites/'index.json').write_text(json.dumps({'account':'90000','enabled':True,'items':[{'id':fid,'file':fid+'.gif','meaning':'开心时亲近','enabled':True}]}))
  await AiocqhttpMessageEvent.send(event('b','77777',text='发个收藏表情'),MessageChain(chain=[Plain('[MEME:'+fid+']')]))
  assert sent[-1][0]=='b' and any(isinstance(x,Image) for x in sent[-1][1].chain)
  await AiocqhttpMessageEvent.send(event('a','88888',text='发个收藏表情'),MessageChain(chain=[Plain('检查[MEME:'+fid+']')]))
  assert not any(isinstance(x,Image) for x in sent[-1][1].chain),'peer cannot send another bot favorite'
  wrong=event('b','99999',text='发个收藏表情');wrong.message_obj.self_id='80000'
  await AiocqhttpMessageEvent.send(wrong,MessageChain(chain=[Plain('检查[MEME:'+fid+']')]))
  assert not any(isinstance(x,Image) for x in sent[-1][1].chain),'new QQ cannot send old account favorites'
  await AiocqhttpMessageEvent.send(event('b','55555',text='发个收藏表情'),MessageChain(chain=[Plain('检查[MEME:laugh]'),Face(id=14)]))
  assert not any(isinstance(x,(Image,Face)) for x in sent[-1][1].chain),'generic reactions and QQ faces cannot impersonate favorites'
  # Exercise the real wrapper and SDK OneBot conversion together. A selected
  # image must not collapse separate conversational bubbles or repeat routing.
  settings['b']['config']['behavior'].update({'splitMessages':True,'sendIntervalMs':0,'typingSimulation':False,'typoEnabled':False})
  text='第一句。[QQ_BREAK]第二句？[QQ_BREAK]第三句！';marker='[MEME:'+pack['items'][0]['id']+']';before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44444',text='正常聊天'),MessageChain(chain=[Reply(id='122'),At(qq='123456'),Plain(text+marker)]))
  bubbles=sent[before:];wire=payloads[before:]
  assert len(bubbles)==3 and [''.join(x.text for x in m.chain if isinstance(x,Plain)) for _,m in bubbles]==['第一句。','第二句？','第三句！']
  assert [sum(isinstance(x,Image) for x in m.chain) for _,m in bubbles]==[0,0,1],'selected image must occur once in the last bubble'
  for cls in (At,Reply):assert [sum(isinstance(x,cls) for x in m.chain) for _,m in bubbles]==[1,0,0],'routing component was lost or repeated'
  assert [sum(x['type']=='image' for x in m) for m in wire]==[0,0,1] and wire[-1][-1]['data']['file'].startswith('base64://')
  decoded=base64.b64decode(wire[-1][-1]['data']['file'][9:]);assert decoded==(root/'templates/meme-packs/blue-fat-fish'/pack['items'][0]['file']).read_bytes(),'wire conversion flattened or replaced the fish animation'
  from PIL import Image as PillowImage
  with PillowImage.open(io.BytesIO(decoded)) as gif:assert gif.n_frames>1,'the real outgoing image must remain animated'
  assert runtime['metrics']['b']['lastMemeDecision']['reason']=='sent'
  # Image-only replies remain one nonempty message; disabling splitting keeps
  # a mixed reply intact. Ordinary quoted text also retains the first target.
  before=len(sent);await AiocqhttpMessageEvent.send(event('b','44445',text='正常聊天'),MessageChain(chain=[Plain(marker)]))
  assert len(sent)==before+1 and [x['type'] for x in payloads[-1]]==['image']
  settings['b']['config']['behavior']['splitMessages']=False;before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44446',text='正常聊天'),MessageChain(chain=[Plain(text+marker)]))
  assert len(sent)==before+1 and any(isinstance(x,Image) for x in sent[-1][1].chain)
  assert '[QQ_BREAK]' not in ''.join(x.text for x in sent[-1][1].chain if isinstance(x,Plain))
  settings['b']['config']['behavior']['splitMessages']=True;before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44447',text='正常聊天'),MessageChain(chain=[Reply(id='123'),At(qq='123456'),Plain(text)]))
  assert len(sent)==before+3 and all(not any(isinstance(x,Image) for x in m.chain) for _,m in sent[before:])
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44449',text='发个收藏表情'),MessageChain(chain=[Plain(text+'[MEME:'+fid+']')]))
  assert len(sent)==before+3 and [sum(isinstance(x,Image) for x in m.chain) for _,m in sent[before:]]==[0,0,1]
  settings['a']['config']['behavior'].update({'splitMessages':True,'sendIntervalMs':0,'typingSimulation':False,'typoEnabled':False});before=len(sent)
  await AiocqhttpMessageEvent.send(event('a','44450',text='发张图片'),MessageChain(chain=[Plain(text+'[IMAGE:gal_test]')]))
  assert len(sent)==before+3 and [sum(isinstance(x,Image) for x in m.chain) for _,m in sent[before:]]==[0,0,1]
  # Model-selected paragraphs, not punctuation, choose one through four
  # messages. Each later bubble adds both configured waits, without extra calls.
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44451',text='正常聊天'),MessageChain(chain=[Plain('一句。又一句？还有一句！')]))
  assert len(sent)==before+1,'punctuation alone must not force three messages'
  settings['b']['config']['behavior']['sendIntervalMs']=800;before=len(sent)
  with patch('behavior.typing_delay',return_value=.4),patch('behavior.asyncio.sleep',new_callable=AsyncMock) as sleeper:
   await AiocqhttpMessageEvent.send(event('b','44452',text='正常聊天'),MessageChain(chain=[Reply(id='124'),At(qq='123456'),Plain('一[QQ_BREAK]二[QQ_BREAK]三[QQ_BREAK]四[QQ_BREAK]五'+marker)]))
   assert [round(c.args[0],3) for c in sleeper.await_args_list]==[.4,1.2,1.2,1.2],'typing wait and inter-message interval must add'
  assert len(sent)==before+4 and [''.join(x.text for x in m.chain if isinstance(x,Plain)) for _,m in sent[before:]]==['一','二','三','四\n\n五']
  assert [sum(isinstance(x,Image) for x in m.chain) for _,m in sent[before:]]==[0,0,0,1]
  for cls in (At,Reply):assert [sum(isinstance(x,cls) for x in m.chain) for _,m in sent[before:]]==[1,0,0,0]
  settings['b']['config']['behavior']['sendIntervalMs']=0
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44453',text='艾特我'),MessageChain(chain=[Plain('来啦[AT:123456][QQ_BREAK]继续聊')]))
  assert len(sent)==before+2 and payloads[before][0]=={'type':'at','data':{'qq':'123456'}}
  assert not any(c['type']=='at' for c in payloads[before+1])
  assert all('[AT:' not in c.get('data',{}).get('text','') for packet in payloads[before:] for c in packet)
  unknown=event('b','44454',text='叫群友')
  async def wrong_member(**kw):return {'user_id':111111}
  unknown.bot=SimpleNamespace(get_group_member_info=wrong_member)
  before=len(sent);await AiocqhttpMessageEvent.send(unknown,MessageChain(chain=[Plain('接着聊[AT:654321][AT:all]')]))
  assert len(sent)==before+1 and all(c['type']!='at' for c in payloads[-1])
  assert '[AT:' not in ''.join(c.get('data',{}).get('text','') for c in payloads[-1])
  before=len(sent)
  await AiocqhttpMessageEvent.send(event('b','44461',text='正常聊天'),MessageChain(chain=[At(qq='123456'),Plain('这一段。\n\n下一段。'+marker)]))
  actual=sent[before:];assert len(actual)==2,'natural paragraphs were merged into a single QQ bubble'
  assert sum(isinstance(c,Image) for _,m in actual for c in m.chain)==1 and any(isinstance(c,Image) for c in actual[-1][1].chain)
  assert sum(isinstance(c,At) for _,m in actual for c in m.chain)==1
  print('PASS natural paragraph bubbles with image once last and mention once first')
  print('PASS model-selected real At components: current sender, one first bubble, no literal markers or unknown/all recipients')
  # A configuration replacement after the first successful bubble must still
  # fence the remaining text and the unsent image, without resurrecting stores.
  before=len(sent)
  def replace():
   settings['b']={**settings['b'],'generation':2};runtime['release']('b')
  after_send=replace
  await AiocqhttpMessageEvent.send(event('b','44448',text='正常聊天'),MessageChain(chain=[Plain(text+marker)]))
  after_send=None
  assert len(sent)==before+1 and not any(isinstance(x,Image) for x in sent[-1][1].chain),'stale trailing media crossed configuration replacement'
  assert 'b' not in runtime['stores'],'partial send recreated a released store'
  print('PASS mixed image bubbles: three sends, image once last, quote/mention once first, image-only, split toggle and stale trailing-send fence')
  print('PASS native unmentioned sticker capture, scoped approval, peer isolation and separate gallery image send')
 finally:runtime['close']()
asyncio.run(run());tmp.cleanup()

"""Owner media follow-ups use the same Qzone authority and receipt boundary."""
import json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.message.components import Image
import qq_actions


class QzoneOwnerMedia(unittest.IsolatedAsyncioTestCase):
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

"""Recent image follow-ups retain real source and tenant/session boundaries."""
import asyncio,sqlite3,sys,time,tempfile,os
from pathlib import Path
from types import SimpleNamespace as NS
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
from astrbot.core.message.components import Image,Plain
import incoming_media,quote_reply
class Event:
 def __init__(self,sender='123',group='555',message='1',parts=None,text='看看'):
  self.sender=sender;self.group=group;self.text=text;self.extra={};self.message_obj=NS(message_id=message,message=parts or [Plain(text)],raw_message={})
 def get_sender_id(self):return self.sender
 def get_self_id(self):return '90000'
 def get_group_id(self):return self.group
 def get_message_str(self):return self.text
 def get_messages(self):return self.message_obj.message
 def get_extra(self,key,default=None):return self.extra.get(key,default)
 def set_extra(self,key,value):self.extra[key]=value
async def run():
 db=sqlite3.connect(':memory:');lease={'botId':'bot-a','generation':7};url='https://gchat.qpic.cn/valid/image'
 source=Event(message='55',text='');source.message_obj.raw_message={'message':[{'type':'image','data':{'url':url,'file':'image.jpg'}}]};quote_reply.observe(db,source,lease,'group:555')
 raw={'message_id':55,'group_id':555,'sender':{'user_id':123},'message':[{'type':'image','data':{'url':url}}]}
 async def get_msg(**kw):return raw
 follow=Event(message='56');follow.bot=NS(get_msg=get_msg)
 result=await incoming_media.attach_recent(follow,db,lease,lambda:True);assert result['message_id']=='55';assert isinstance(follow.get_messages()[-1],Image);assert follow.message_obj.raw_message=={}
 for event,settings in [(Event(sender='456'),lease),(Event(group='666'),lease),(Event(),{**lease,'generation':8})]:
  event.bot=NS(get_msg=get_msg);assert await incoming_media.attach_recent(event,db,settings,lambda:True) is None
 late=Event();late.bot=NS(get_msg=get_msg);assert await incoming_media.attach_recent(late,db,lease,lambda:False) is None
 db.execute('UPDATE pipeline_quote_sources SET at=?',(time.time()-46,));assert await incoming_media.attach_recent(Event(),db,lease,lambda:True) is None
 print('PASS recent image source, sender, scope, generation, late cancellation and expiry')
asyncio.run(run())

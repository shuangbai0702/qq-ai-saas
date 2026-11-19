"""Original OneBot -> native SDK -> shared Journal -> final HTTP wire.

Optional real-model credentials are supplied only in the process environment.
All inputs are synthetic; no QQ sends and no production Journal writes.
"""
import asyncio,copy,json,os,sys,time,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from activity_main_journal_test import Activity,ProviderOpenAIOfficial


class VoiceWire(Activity):
    async def test_voice_text_order_completeness_and_final_wire(self):
        import httpx,shared_context
        from aiocqhttp import Event
        from native_ingress import bind,RawInput
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import AiocqhttpAdapter
        from astrbot.core.platform.astr_message_event import current_message_event
        from context_protocol import SOURCE,model_json,source
        lease=self.settings['qq'];st=self.st;gate=asyncio.Event();started=asyncio.Event();calls=[]
        self.runtime['metrics'].setdefault('qq',{})['requests']=0
        async def protocol(action,**data):
            self.assertEqual(action,'fetch_ptt_text');calls.append(data);started.set();await gate.wait()
            return {'text':'明天几点开始？'}
        platform=AiocqhttpAdapter.__new__(AiocqhttpAdapter);platform.bot=NS(call_action=protocol)
        platform.meta=lambda:self.core.platform_manager.get_insts()[0].meta()
        bind(platform,lambda raw:self.runtime['acquire_raw_ingress']('qq',raw),
             prepare=lambda raw:__import__('voice_inputs').prepare(st,raw,protocol))
        def raw(identifier,parts):
            return Event.from_payload({'post_type':'message','message_type':'private','sub_type':'friend',
                'self_id':90000000,'user_id':12345678,'message_id':identifier,'time':int(time.time()),
                'sender':{'user_id':12345678,'nickname':'Synthetic person'},'message':parts,'raw_message':''})
        def stage(message):
            event=platform.create_event(message);event.set_extra('_hosted_lease',lease)
            event.set_extra('_hosted_generation',1)
            shared_context.observe(st.db,event,lease['config']);st.slot_changed.set()
            return event
        voice=raw(101,[{'type':'record','data':{'file':'synthetic-voice.silk'}}])
        pending=asyncio.create_task(platform.convert_message(voice));await started.wait()
        text=raw(102,[{'type':'text','data':{'text':'请只回答：明天八点。'}}])
        event=stage(await platform.convert_message(text));work=st.pipeline.bind(event,lease)
        event.set_extra('_hosted_context',model_json({'identity':{'scope':work.scope,'sender_id':'12345678','self_id':'90000000','owner':False},'state':{}}))
        real=json.loads(os.environ.get('FISHBOT_VOICE_MODEL_CONFIG','null'))
        if real:
            await self.provider.client.close()
            transport=httpx.AsyncHTTPTransport()
            class Capture(httpx.AsyncBaseTransport):
                async def handle_async_request(_,request):
                    self.requests.append(json.loads(request.content))
                    return await transport.handle_async_request(request)
                async def aclose(_):await transport.aclose()
            with patch.object(ProviderOpenAIOfficial,'_create_http_client',lambda p,c:httpx.AsyncClient(transport=Capture(),timeout=45)):
                self.provider=ProviderOpenAIOfficial({'id':'voice-synthetic','type':'openai_chat_completion','model':real['model'],'api_base':real['baseUrl'],'key':[real['key']],'custom_extra_body':{'max_tokens':64}}, {})
            self.core.provider_manager.inst_map['platform-model']=self.provider
        async def request():
            token=current_message_event.set(event)
            try:return await self.provider.text_chat(system_prompt='Synthetic stable persona',contexts=[],prompt=SOURCE+model_json({'scope':work.scope,'sender_id':work.subject,'event_id':work.event_id})+'\n'+event.get_message_str(),func_tool=None)
            finally:current_message_event.reset(token)
        task=asyncio.create_task(request());await asyncio.sleep(0)
        self.assertEqual(self.requests,[],'pool expiry cannot submit incomplete input')
        gate.set();stage(await pending);response=await asyncio.wait_for(task,60)
        self.assertEqual(len(calls),1);self.assertEqual(len(self.requests),1)
        wire=self.requests[0]['messages'];events=[(source(m),m) for m in wire if source(m)]
        self.assertEqual([str(origin['event_id']) for origin,m in events],['101','102'])
        payload=json.dumps(wire,ensure_ascii=False)
        self.assertEqual(payload.count('[QQ语音] 明天几点开始？'),1)
        self.assertNotIn('synthetic-voice.silk',payload);self.assertNotIn('fetch_ptt_text',payload)
        self.assertFalse(any(part.get('type')=='input_audio' for m in wire if isinstance(m.get('content'),list) for part in m['content']))
        self.assertEqual(set(st.model_journals),{'bot_shared'})
        self.assertEqual(st.db.execute('SELECT payload FROM pipeline_work_inputs WHERE event=?',('101',)).fetchone()[0],json.dumps(voice,ensure_ascii=False,sort_keys=True,separators=(',',':')))
        print('VOICE_WIRE_RESULT '+json.dumps({'realModel':bool(real),'model':real['model'] if real else 'fixture','requests':len(self.requests),'events':['101','102'],'voiceCopies':1,'wireChars':len(payload),'responsePresent':bool(response.completion_text)},ensure_ascii=True))


if __name__=='__main__':unittest.main(defaultTest='VoiceWire.test_voice_text_order_completeness_and_final_wire')

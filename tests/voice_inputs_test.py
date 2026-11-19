"""Controlled protocol delays: no QQ account, model or private chat involved."""
import asyncio,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import voice_inputs as voice
from native_ingress import bind


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def store(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''CREATE TABLE pipeline_work_inputs(scope,event,payload,voice,seen DEFAULT 0,status DEFAULT 'queued');
          CREATE TABLE fishbot_shared_inputs(scope,event);''')
        return SimpleNamespace(db=db,slot_changed=asyncio.Event())

    def raw(self,st,id='1'):
        raw={'message_id':id,'user_id':123456,'self_id':900000,'message_type':'private','message':[{'type':'record','data':{'file':'private-path'}}]}
        st.db.execute('INSERT INTO pipeline_work_inputs(scope,event,payload) VALUES(?,?,?)',('private:123456',id,json.dumps(raw)))
        voice.retain(st.db,'private:123456',id,raw);st.db.commit()
        return raw

    async def test_pending_voice_blocks_later_text_until_observed_once(self):
        st=self.store();raw=self.raw(st);barrier=asyncio.Event();calls=[]
        async def call(op,**kw):
            calls.append((op,kw));await barrier.wait();return {'text':'明天几点开始？'}
        conversion=asyncio.create_task(voice.prepare(st,raw,call))
        writer=asyncio.create_task(voice.ready(st))
        await asyncio.sleep(0)
        self.assertFalse(writer.done())
        # Simulate expiration of the original five-second pool. Its Writer
        # still cannot pass, and conversion completion alone is insufficient.
        barrier.set();result=await conversion
        self.assertFalse(writer.done())
        self.assertEqual(result['message'],[{'type':'text','data':{'text':'[QQ语音] 明天几点开始？'}}])
        st.db.execute('INSERT INTO fishbot_shared_inputs VALUES(?,?)',('private:123456','1'));st.slot_changed.set()
        await asyncio.wait_for(writer,1)
        async def forbidden(*a,**kw):raise AssertionError('duplicate ASR')
        self.assertEqual(await voice.prepare(st,raw,forbidden),result)
        self.assertEqual(len(calls),1)
        self.assertEqual(json.loads(st.db.execute('SELECT payload FROM pipeline_work_inputs').fetchone()[0]),raw)
        self.assertNotIn('private-path',str(result['message']))

    async def test_soft_timeout_can_receive_late_success(self):
        st=self.store();raw=self.raw(st);release=asyncio.Event()
        async def call(*a,**kw):await release.wait();return {'text':'迟到但真实'}
        with patch.object(voice,'SOFT_TIMEOUT',.005),patch.object(voice,'HARD_TIMEOUT',1):
            task=asyncio.create_task(voice.prepare(st,raw,call))
            async def uncertain():
                while voice.load(st.db,'private:123456','1')['state']=='pending':await asyncio.sleep(0)
            await asyncio.wait_for(uncertain(),1)
            self.assertEqual(voice.load(st.db,'private:123456','1')['state'],'unknown')
            self.assertFalse(task.done());release.set();result=await task
        self.assertEqual(result['message'][0]['data']['text'],'[QQ语音] 迟到但真实')

    async def test_definitive_failure_and_unknown_are_distinct(self):
        for error,state in [(ValueError('消息中不包含语音'),'failed'),(TimeoutError('timeout'),'unknown')]:
            st=self.store();raw=self.raw(st)
            async def call(*a,**kw):raise error
            await voice.prepare(st,raw,call)
            self.assertEqual(voice.load(st.db,'private:123456','1')['state'],state)

    async def test_two_voices_hold_writer_until_both_staged_and_hard_timeout_is_unknown(self):
        st=self.store();first,second=self.raw(st,'1'),self.raw(st,'2');gate=asyncio.Event()
        async def quick(*args,**kw):return {'text':'第一条'}
        async def never(*args,**kw):await gate.wait();return {'text':'too late'}
        with patch.object(voice,'SOFT_TIMEOUT',.005),patch.object(voice,'HARD_TIMEOUT',.01):
            writer=asyncio.create_task(voice.ready(st))
            await voice.prepare(st,first,quick)
            st.db.execute('INSERT INTO fishbot_shared_inputs VALUES(?,?)',('private:123456','1'));st.slot_changed.set()
            await asyncio.sleep(0);self.assertFalse(writer.done())
            result=await voice.prepare(st,second,never)
            self.assertEqual(result['message'][0]['data']['text'],'[QQ语音：转写结果未知]')
            self.assertTrue(voice.load(st.db,'private:123456','2')['finished'])
            self.assertFalse(writer.done())
            st.db.execute('INSERT INTO fishbot_shared_inputs VALUES(?,?)',('private:123456','2'));st.slot_changed.set()
            await asyncio.wait_for(writer,1)

    async def test_restart_does_not_repeat_uncertain_remote_call(self):
        st=self.store();raw=self.raw(st)
        st.db.execute('UPDATE pipeline_work_inputs SET voice=?',(json.dumps({'state':'pending','attempts':1,'owner':'old-process'}),))
        async def forbidden(*a,**kw):raise AssertionError('repeated after restart')
        result=await voice.prepare(st,raw,forbidden)
        self.assertEqual(result['message'][0]['data']['text'],'[QQ语音：转写结果未知]')

    async def test_late_result_cannot_replace_committed_or_retired_input(self):
        st=self.store();self.raw(st)
        st.db.execute('UPDATE pipeline_work_inputs SET voice=?,seen=1',(json.dumps({'state':'unknown','attempts':1,'owner':'old'}),))
        self.assertFalse(voice.finish(st.db,'private:123456','1','old','success','wrong late text'))
        st.db.execute('UPDATE pipeline_work_inputs SET seen=0,payload=NULL')
        self.assertFalse(voice.finish(st.db,'private:123456','1','old','success','wrong late text'))

    async def test_distinct_bots_with_same_message_id_are_independent(self):
        a,b=self.store(),self.store();rawa,rawb=self.raw(a),self.raw(b)
        gate=asyncio.Event()
        async def slow(*args,**kw):await gate.wait();return {'text':'A'}
        async def fast(*args,**kw):return {'text':'B'}
        task=asyncio.create_task(voice.prepare(a,rawa,slow))
        self.assertEqual((await voice.prepare(b,rawb,fast))['message'][0]['data']['text'],'[QQ语音] B')
        self.assertFalse(task.done());gate.set();await task

    async def test_concurrent_duplicate_preparation_shares_one_remote_attempt(self):
        st=self.store();raw=self.raw(st);gate=asyncio.Event();calls=[]
        async def call(*args,**kw):calls.append(kw);await gate.wait();return {'text':'唯一转写'}
        first=asyncio.create_task(voice.prepare(st,raw,call));second=asyncio.create_task(voice.prepare(st,raw,call))
        await asyncio.sleep(0);gate.set()
        self.assertEqual(await first,await second);self.assertEqual(len(calls),1)
        await asyncio.sleep(0);self.assertFalse(st._voice_inflight)

    async def test_native_live_and_recovery_share_conversion_and_no_sdk_record(self):
        st=self.store();raw=self.raw(st);converted=[];released=[]
        async def original(value):converted.append(value);return SimpleNamespace(raw_message=value)
        async def call(*a,**kw):return {'text':'你好'}
        platform=SimpleNamespace(convert_message=original)
        bind(platform,lambda raw:lambda:released.append(True),prepare=lambda raw:voice.prepare(st,raw,call))
        first=await platform.convert_message(raw);second=await platform.convert_retained_message(raw)
        self.assertEqual(first.raw_message,raw);self.assertEqual(second.raw_message,raw)
        self.assertEqual(released,[True]);self.assertEqual(len(converted),2)
        self.assertTrue(all(s['type']!='record' for value in converted for s in value['message']))


if __name__=='__main__':unittest.main()

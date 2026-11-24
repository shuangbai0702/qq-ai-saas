"""Automated wire-cleanliness and context-hygiene tests (Phase 5)."""
import asyncio,json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters/astrbot'),str(ROOT/'references/astrbot'),str(ROOT/'tests')]

from context_protocol import (
    CHARACTER_CARD_MARKER,EVENT_CONTEXT_CURRENT,PERIODIC_REMINDER,
    PERSONA_REFERENCE,READONLY_CURRENT,SOURCE,is_card,is_persona_reference,model_json,
)
from context_transport import decision_request,foreground_system,request_context_view
from group_freshness import GroupFreshness
import model_cache
import periodic_guidance
import bot_pipeline


class WireCleanlinessTest(unittest.TestCase):
    def test_foreground_wire_has_single_platform_persona_and_cards(self):
        cards=[
            {'id':'c1','name':'口癖','kind':'speech','content':'偶尔说喵','enabled':True},
            {'id':'c2','name':'禁用卡','kind':'custom','content':'不该出现','enabled':False},
        ]
        platform=foreground_system({'webSearch':True})
        sdk_contexts=[
            {'role':'system','content':'我是小鱼，爱吃炸鸡。'},
            {'role':'user','content':SOURCE+model_json({'scope':'private:10001','sender_id':'10001','event_id':'e1','at':1000})+'\n你好'},
        ]
        context=model_json({'identity':{'sender_id':'10001','scope':'private:10001'},'memory_foundation':{'core_note':{'text':''},'facts':[],'conversation_summaries':[]},'state':{}})
        stable,history=request_context_view(
            '我是小鱼，爱吃炸鸡。',sdk_contexts,'你好','策略文本','当前规则',context,
            incremental_policy=True,platform_system=platform,character_cards=cards,
        )
        wire=[{'role':'system','content':stable}]+history
        self.assertEqual(sum(1 for m in wire if m.get('role')=='system' and str(m.get('content','')).startswith('[FishBot 平台基础规则]\n')),1)
        self.assertEqual(sum(1 for m in wire if is_persona_reference(m)),1)
        self.assertEqual(sum(1 for m in wire if is_card(m)),1)
        self.assertNotIn('不该出现',json.dumps(wire,ensure_ascii=False))
        self.assertIn('不要过度理性。',stable)

    def test_regeneration_from_committed_journal_base_never_duplicates_persona_or_wraps_platform(self):
        cards=[{'id':'c1','name':'口癖','kind':'speech','content':'偶尔说喵','enabled':True}]
        platform=foreground_system({'webSearch':False})
        context=model_json({'identity':{'sender_id':'10001','scope':'private:10001'},'state':{}})
        stable,history=request_context_view(
            '我是小鱼。',[{'role':'system','content':'我是小鱼。'}],'你好','策略','规则',context,
            incremental_policy=True,platform_system=platform,character_cards=cards,
        )
        committed_base=[{'role':'system','content':stable}]+history
        # Simulate admit_public regeneration passing committed_base as contexts:
        stable2,history2=request_context_view(
            '我是小鱼。',committed_base,None,'策略','规则',context,
            incremental_policy=True,platform_system=platform,character_cards=cards,
        )
        wire2=[{'role':'system','content':stable2}]+history2
        persona_msgs=[m for m in wire2 if is_persona_reference(m)]
        self.assertEqual(len(persona_msgs),1)
        self.assertNotIn('[FishBot 平台基础规则]',persona_msgs[0]['content'])
        self.assertEqual(sum(1 for m in wire2 if is_card(m)),1)
        self.assertEqual(sum(1 for m in wire2 if m.get('role')=='system' and str(m.get('content','')).startswith('[FishBot 平台基础规则]\n')),1)

    def test_shared_context_conversation_context_omits_empty_legacy_arrays(self):
        with tempfile.TemporaryDirectory() as tmp:
            from behavior_store import BehaviorStore
            store=BehaviorStore(str(Path(tmp)/'behavior.sqlite'))
            store.shared_context=True
            try:
                cfg={'memory':True,'ownerUin':'10001'}
                ctx=bot_pipeline.conversation_context(store,cfg,'private:10001','10001','你好',time.time())
                for legacy_key in ('group_round_history','conversation_timeline','recent_messages','recent_cross_session_messages','ambient_group_messages'):
                    self.assertNotIn(legacy_key,ctx)
                self.assertIn('memory_foundation',ctx)
                self.assertIn('dialogue',ctx)
                self.assertIn('active_agreements',ctx)
            finally:
                store.close()

    def test_decision_prefix_fields_reads_split_reference_messages(self):
        req1=decision_request({
            'persona':'我是小鱼','owner_id':'10001','scope':'group:1',
            'memory':{'memory_foundation':{'facts':[{'k':'a','v':'b'}]},'cross_scope_guidance':'guidance','recent_events':[1]},
            'latest_messages':[{'text':'hi'}],
        },'判断职责',reference_first=True)
        msgs1=[{'role':'system','content':req1['system_prompt']},*req1['contexts'],{'role':'user','content':req1['prompt']}]
        key=('t','b','s','group_freshness','m','ep')
        f1=model_cache.decision_prefix_fields(key,msgs1)
        self.assertIn('referenceChars',f1)
        self.assertIn('referenceSha',f1)
        self.assertFalse(f1['userPrefixKnown'])

        req2=decision_request({
            'persona':'我是小鱼','owner_id':'10001','scope':'group:1',
            'memory':{'memory_foundation':{'facts':[{'k':'a','v':'b'}]},'cross_scope_guidance':'guidance','recent_events':[1,2]},
            'latest_messages':[{'text':'hello again'}],
        },'判断职责',reference_first=True)
        msgs2=[{'role':'system','content':req2['system_prompt']},*req2['contexts'],{'role':'user','content':req2['prompt']}]
        f2=model_cache.decision_prefix_fields(key,msgs2)
        self.assertTrue(f2['userPrefixKnown'])
        self.assertEqual(f1['referenceSha'],f2['referenceSha'])
        self.assertGreater(f2['userPrefixChars'],f1['referenceChars'])

    def test_group_freshness_parses_think_wrapped_json(self):
        gf=GroupFreshness()
        now=time.monotonic()
        gf.observe('group:1','e1','20001','新消息',now=now)
        async def call(_payload):
            return '<think>先想一下是否合适</think>\n```json\n{"action":"keep","reason":"仍然贴切","responded_events":["e1"],"ignored_events":[]}\n```'
        result=asyncio.run(gf.check('group:1',0,now-1.0,'e0','草稿','',call,lambda:True))
        self.assertEqual(result['action'],'keep')
        self.assertEqual(result['responded_events'],['e1'])

    def test_periodic_guidance_caps_goals_and_agreements_and_preserves_reminder(self):
        self.assertIn('不要过度理性。',PERIODIC_REMINDER)
        with tempfile.TemporaryDirectory() as tmp:
            from behavior_store import BehaviorStore
            store=BehaviorStore(str(Path(tmp)/'behavior.sqlite'))
            store.shared_context=True
            try:
                periodic_guidance.initialize(store.db)
                store.db.execute("INSERT INTO fishbot_guidance_cadence VALUES('bot_shared',7)")
                import agreement_state
                agreement_state.initialize(store.db)
                now_ts=time.time()
                for i in range(7):
                    store.db.execute(
                        "INSERT INTO memory_agreements(id,revision,topic,detail,speaker,participants,source_scope,source_event,audiences,status,due,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f'ag-{i}',1,f'topic-{i}',f'约定{i}','10001','["10001","bot"]','private:10001',f'ev-{i}','["private:10001"]','accepted','',now_ts+i,now_ts+i)
                    )
                store.db.commit()

                extras={}
                class FakeEvent:
                    def set_extra(self,k,v):extras[k]=v
                    def get_extra(self,k,d=None):return extras.get(k,d)
                event=FakeEvent()
                event.set_extra('_hosted_pipeline',NS(scope='private:10001',event_id='ev7',subject='10001'))

                import autonomous_goals
                orig_tools=autonomous_goals.tools
                async def fake_read(_ctx):
                    return json.dumps({'goals':[{'id':f'g{i}','title':f'目标{i}','status':'active','revision':1} for i in range(6)]})
                autonomous_goals.tools=lambda *_a,**_kw:[NS(name='read_autonomous_goals',call=fake_read)]
                try:
                    asyncio.run(periodic_guidance.prepare_goals(store,event,{'config':{'behavior':{'ownerUin':'10001'}}},lambda:True))
                finally:
                    autonomous_goals.tools=orig_tools
                attached=event.get_extra('_hosted_periodic_guidance')
                self.assertIsNotNone(attached)
                self.assertIn('不要过度理性。',attached)
                payload=json.loads(attached.split('）：',1)[1])
                self.assertEqual(len(payload['goals']),3)
                self.assertEqual(len(payload['agreements']),5)
            finally:
                store.close()


if __name__=='__main__':
    unittest.main()

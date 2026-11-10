"""Existing SQLite and real planner paths; no paid requests or QQ sends."""
import json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import autonomy_test
import autonomy
from bot_pipeline import decision_memory,decision_version
from context_transport import decision_request

class CognitiveProjection(unittest.IsolatedAsyncioTestCase):
    setUp=autonomy_test.AutonomyTest.setUp
    tearDown=autonomy_test.AutonomyTest.tearDown
    contact=autonomy_test.AutonomyTest.contact
    def test_relation_impression_trust_zero_value_identity_and_source_survive_projection(self):
        for scope,person,affinity,trust,impression in [('bot_shared','11111111',0,.7,'认真而敏感'),('group:12345','22222222',2,-.3,'需要重新建立信任'),('private:33333333','33333333',7,.9,'其他人物私聊认知')]:
            self.db.execute('INSERT INTO relations(scope,subject,affinity,trust,impression,last_seen) VALUES(?,?,?,?,?,?)',(scope,person,affinity,trust,impression,self.now))
        memory=decision_memory(self.db,self.cfg,'private:11111111','11111111',self.now)
        self.assertEqual([(r['source_scope'],r['subject'],r['affinity'],r['trust'],r['impression']) for r in memory['relations']],[('bot_shared','11111111',0,.7,'认真而敏感')])
        before=decision_request({'persona':'fixed','owner_id':'11111111','owner_memory':memory},'判断职责',reference_first=True)
        old_version=decision_version(self.db,'private:11111111')
        self.db.execute("UPDATE relations SET trust=.2,impression='新认识' WHERE subject='11111111'")
        self.assertNotEqual(old_version,decision_version(self.db,'private:11111111'))
        after=decision_request({'persona':'fixed','owner_id':'11111111','owner_memory':decision_memory(self.db,self.cfg,'private:11111111','11111111',self.now)},'判断职责',reference_first=True)
        self.assertEqual(before['system_prompt'],after['system_prompt'])
        self.assertIn('新认识',json.dumps(after,ensure_ascii=False));self.assertNotIn('其他人物私聊认知',json.dumps(after,ensure_ascii=False))
        group=decision_memory(self.db,self.cfg,'group:12345','22222222',self.now,subjects=['11111111'])
        self.assertEqual({r['subject'] for r in group['relations']},{'11111111','22222222'})
    def test_decision_continuity_is_attributed_time_bound_and_privacy_forgetting_clears_prose(self):
        from memory_reflection import invalidate
        self.contact()
        with self.db:autonomy.record_decision(self.db,self.cfg,'private:11111111','decision-1',{'contact':False,'topic':'先休息','reason':'主人刚说要睡觉'},False,self.now,'owner')
        memory=decision_memory(self.db,self.cfg,'private:11111111','11111111',self.now+1)
        entry=memory['recent_autonomous_decisions'][0]
        self.assertEqual(entry['reason'],'主人刚说要睡觉');self.assertEqual(entry['basis_last_user'],self.now-10000)
        self.assertFalse(entry['chose_contact'])
        self.assertEqual(decision_memory(self.db,self.cfg,'private:22222222','22222222',self.now+1)['recent_autonomous_decisions'],[])
        self.assertEqual(decision_memory(self.db,self.cfg,'private:11111111','11111111',self.now+86401)['recent_autonomous_decisions'],[])
        with self.db:invalidate(self.db,'private:11111111','11111111')
        self.assertEqual(decision_memory(self.db,self.cfg,'private:11111111','11111111',self.now+1)['recent_autonomous_decisions'],[])

    async def test_auth_failure_pauses_owner_phase_and_revision_releases_it(self):
        self.contact();calls=[]
        class AuthenticationError(Exception):pass
        async def fail(**kw):calls.append(kw);raise AuthenticationError('fixture credential rejected')
        provider=NS(text_chat=fail);lease={'botId':'bot','generation':1,'revision':1}
        try:
            with patch('autonomy.time.time',return_value=self.now+3600):
                self.store.planner.maybe_start(self.cfg,provider,lambda:True,self.event,self.metrics,now=self.now+3600,lease=lease);await self.store.planner.task
                self.assertIsNone(self.metrics['lastOwnerDecision']['contact'])
                self.assertEqual(self.metrics['lastOwnerDecision']['failureClass'],'auth')
                self.db.execute('UPDATE autonomy_owner SET checked=0');self.db.commit()
                self.store.planner.maybe_start(self.cfg,provider,lambda:True,self.event,self.metrics,now=self.now+3601,lease=lease)
                self.assertEqual(len(calls),1)
                self.store.planner.maybe_start(self.cfg,provider,lambda:True,self.event,self.metrics,now=self.now+3602,lease={**lease,'revision':2});await self.store.planner.task
                self.assertEqual(len(calls),2)
        finally:autonomy._auth_failures.clear()
    async def test_length_is_failure_even_if_truncated_response_happens_to_be_valid_json(self):
        self.contact()
        async def answer(**kw):return NS(completion_text='{"contact":false,"topic":""}',raw_completion={'choices':[{'finish_reason':'length','message':{}}]})
        self.store.planner.maybe_start(self.cfg,NS(text_chat=answer),lambda:True,self.event,self.metrics,now=self.now+3600)
        await self.store.planner.task
        self.assertEqual(self.metrics['lastOwnerDecision']['reason'],'owner_output_truncated')
        self.assertIsNone(self.metrics['lastOwnerDecision']['contact'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM autonomy_decisions').fetchone()[0],0)

if __name__=='__main__':unittest.main()

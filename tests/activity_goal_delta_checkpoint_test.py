"""B inheritance retires through the real 35-round semantic transaction."""
import copy,contextvars,sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import activity_round_checkpoint_test as fixture
from activity_goal_delta_test import snapshot,service


class GoalCheckpoint(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ActivationCheckpoint.asyncSetUp
    asyncTearDown=fixture.ActivationCheckpoint.asyncTearDown
    summarize=fixture.ActivationCheckpoint.summarize

    async def test_35_retirement_restart_rebuilds_full_goal_action_effect_baseline(self):
        from activation_rounds import record
        from readonly_journal import ReadonlyJournal,Prepared
        from summary_material import snapshots,remember
        from activity_context import tail_service,committed_goals,_goal_service
        from types import SimpleNamespace as NS
        scope=fixture.fixture.SCOPE;db=self.store.db;value=snapshot()
        journal=ReadonlyJournal(db,scope)
        self.store.reflection.binding=(self.cfg,self.provider,lambda:True,self.metrics,contextvars.ContextVar('goal_checkpoint',default=None))
        for number in range(1,36):
            event='goal-activation-'+str(number)
            db.execute('INSERT INTO pipeline_actions(id,scope,state,at) VALUES(?,?,?,?)',(event,scope,'completed',time.time()))
            record(db,scope,event,event,source_scope=scope,subject='90000000')
            user=service(value,event,scope);assistant={'role':'assistant','content':'[NO_REPLY]'}
            remember(db,scope,snapshots([user,assistant],scope));db.commit()
            base=copy.deepcopy(journal.last.base) if journal.last else [{'role':'system','content':'STABLE_MAIN_PERSONA'}]
            base.append(user);journal.serial+=1
            prepared=Prepared(journal.serial,None,copy.deepcopy(base),copy.deepcopy(base),{},1,time.time(),'fixture')
            tail_service(prepared,journal,NS(scope=scope,event_id=event))
            self.assertEqual(committed_goals(prepared.wire,scope)[0],value)
            prepared.base.append(assistant);prepared.wire.append(copy.deepcopy(assistant))
            prepared.main_conversation=True;self.assertTrue(journal.commit(prepared))
        self.store.model_journals[scope]=journal
        await self.summarize()
        self.assertEqual(db.execute('SELECT count(*) FROM memory_reconstruction_checkpoints WHERE round_count=35').fetchone()[0],1,self.metrics)
        restored=ReadonlyJournal(db,scope)
        self.assertTrue(restored.last.main_conversation)
        self.assertEqual(committed_goals(restored.last.wire,scope),(None,[]))
        before=copy.deepcopy(restored.last.wire);event='goal-activation-36'
        messages=before+[service(value,event,scope)];restored.serial+=1
        prepared=Prepared(restored.serial,None,copy.deepcopy(messages),messages,{},1,time.time(),'fixture')
        tail_service(prepared,restored,NS(scope=scope,event_id=event))
        self.assertEqual(prepared.wire[:len(before)],before)
        current=_goal_service(prepared.wire[-1],scope)['currentGoals']
        self.assertEqual(current['mode'],'baseline');self.assertEqual(current['snapshot'],value)
        self.assertEqual(committed_goals(prepared.wire,scope)[0],value)


if __name__=='__main__':unittest.main()

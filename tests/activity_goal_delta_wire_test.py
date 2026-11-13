"""Actual pinned Provider HTTP current-goal inputs, with no paid model calls."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import activity_main_journal_test as fixture
from activity_context import committed_goals,_goal_service,necessary_goals
from activity_goal_delta_test import snapshot_with_history,assert_goal_only


class GoalDeltaWire(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Activity.asyncSetUp
    asyncTearDown=fixture.Activity.asyncTearDown

    async def activate(self,value,*,continuation=False):
        with patch('activity_context.visible_goals',return_value=copy.deepcopy(value)):
            return await fixture.Activity.activate(self,continuation=continuation)

    async def test_actual_wire_restart_tools_stage_rollback_and_changes(self):
        value=snapshot_with_history();self.tool_step=True
        result=await self.activate(value);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(self.requests),2)
        messages=self.requests[0]['messages']
        self.assertEqual(committed_goals(messages,'bot_shared')[0],necessary_goals(value))
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',json.dumps(messages))
        initial=next(payload['currentGoals'] for m in reversed(messages) if (payload:=_goal_service(m,'bot_shared')))
        assert_goal_only(self,initial,value)
        self.assertEqual(self.requests[1]['messages'][:len(messages)],messages)
        appended=self.requests[1]['messages'][len(messages):]
        self.assertEqual([m['role'] for m in appended],['assistant','tool'])
        self.assertEqual(appended[0]['tool_calls'][0]['id'],appended[1]['tool_call_id'])
        journal=self.st.model_journals['bot_shared'];saved=copy.deepcopy(journal.last.wire)
        self.st.model_journals.clear()
        result=await self.activate(value);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(self.requests[-1]['messages'][:len(saved)],saved)
        self.assertEqual(committed_goals(self.requests[-1]['messages'],'bot_shared')[0],necessary_goals(value))
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',json.dumps(self.requests[-1]['messages']))
        current=next(payload['currentGoals'] for m in reversed(self.requests[-1]['messages']) if (payload:=_goal_service(m,'bot_shared')))
        assert_goal_only(self,current,value)
        changed=copy.deepcopy(value);changed['goals'][0].update(revision=8,progress='Genuine revision')
        before=copy.deepcopy(self.st.model_journals['bot_shared'].last.wire);requests=len(self.requests)
        with patch('activation_rounds.record',side_effect=RuntimeError('stage rollback')):
            failure=await self.activate(changed)
        self.assertNotEqual(failure['status'],'completed');self.assertEqual(len(self.requests),requests)
        self.st.model_journals.clear()
        result=await self.activate(changed);self.assertEqual(result['status'],'completed',result)
        final=self.requests[-1]['messages']
        self.assertEqual(final[:len(before)],before)
        self.assertEqual(committed_goals(final,'bot_shared')[0],necessary_goals(changed))
        current=next(payload['currentGoals'] for m in reversed(final) if (payload:=_goal_service(m,'bot_shared')))
        assert_goal_only(self,current,changed)
        self.assertEqual(current['goals'][0]['revision'],8)
        self.assertEqual(final[0],messages[0])
        before_continuation=copy.deepcopy(self.st.model_journals['bot_shared'].last.wire)
        rounds=self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0]
        continued=await self.activate(changed,continuation=True)
        self.assertEqual(continued['status'],'completed',continued)
        continuation_wire=self.requests[-1]['messages']
        self.assertEqual(continuation_wire[:len(before_continuation)],before_continuation)
        self.assertEqual(committed_goals(continuation_wire,'bot_shared')[0],necessary_goals(changed))
        activation=next(payload for m in reversed(continuation_wire) if (payload:=_goal_service(m,'bot_shared')))
        self.assertEqual(activation['kind'],'autonomous_tool_continuation')
        assert_goal_only(self,activation['currentGoals'],changed)
        self.assertEqual(activation['continuation']['result'],{'status':'completed','evidence':['actual result']})
        self.assertEqual(self.st.db.execute('SELECT count(*) FROM memory_rounds').fetchone()[0],rounds)
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',json.dumps(continuation_wire))


if __name__=='__main__':unittest.main()

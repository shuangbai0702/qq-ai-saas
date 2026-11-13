"""Actual pinned Provider HTTP requests for B, no paid model or external writes."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import activity_main_journal_test as fixture
from activity_context import committed_goals,GOAL_FORMAT,_goal_service
from activity_goal_delta_test import snapshot


class GoalDeltaWire(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.Activity.asyncSetUp
    asyncTearDown=fixture.Activity.asyncTearDown

    async def activate(self,value,*,continuation=False):
        with patch('activity_context.visible_goals',return_value=copy.deepcopy(value)):
            return await fixture.Activity.activate(self,continuation=continuation)

    async def test_actual_wire_restart_tools_stage_rollback_and_changes(self):
        value=snapshot();self.tool_step=True
        result=await self.activate(value);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(self.requests),2)
        messages=self.requests[0]['messages']
        self.assertEqual(committed_goals(messages,'bot_shared')[0],value)
        self.assertIn(GOAL_FORMAT,json.dumps(messages,ensure_ascii=False))
        self.assertEqual(self.requests[1]['messages'][:len(messages)],messages)
        journal=self.st.model_journals['bot_shared'];saved=copy.deepcopy(journal.last.wire)
        self.st.model_journals.clear()
        result=await self.activate(value);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(self.requests[-1]['messages'][:len(saved)],saved)
        self.assertEqual(committed_goals(self.requests[-1]['messages'],'bot_shared')[0],value)
        current=next(payload['currentGoals'] for m in reversed(self.requests[-1]['messages']) if (payload:=_goal_service(m,'bot_shared')))
        self.assertEqual(current['mode'],'delta');self.assertEqual(current['changes'],{})
        changed=copy.deepcopy(value);changed['goals'][0].update(revision=8,progress='Genuine revision')
        before=copy.deepcopy(self.st.model_journals['bot_shared'].last.wire);requests=len(self.requests)
        with patch('activation_rounds.record',side_effect=RuntimeError('stage rollback')):
            failure=await self.activate(changed)
        self.assertNotEqual(failure['status'],'completed');self.assertEqual(len(self.requests),requests)
        self.st.model_journals.clear()
        result=await self.activate(changed);self.assertEqual(result['status'],'completed',result)
        final=self.requests[-1]['messages']
        self.assertEqual(final[:len(before)],before)
        self.assertEqual(committed_goals(final,'bot_shared')[0],changed)
        self.assertEqual(final[0],messages[0])
        await self.activate(changed,continuation=True)
        self.assertEqual(committed_goals(self.requests[-1]['messages'],'bot_shared')[0],changed)


if __name__=='__main__':unittest.main()

"""Current-goal activation input, legacy codec and immutable physical prefixes."""
import copy,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from activity_context import goal_delta,apply_goal_delta,tail_service,committed_goals,visible_goals,necessary_goals
from context_protocol import SOURCE,model_json
from readonly_journal import ReadonlyJournal,Prepared,history_usage


def snapshot():
    return {'goals':[{'id':'goal-exact-uuid','revision':7,'title':'Original intent','source':{'scope':'private:12345678','originalGoal':'Never relax this constraint'},'progress':'Actual finding'}],
            'actions':[{'id':'action-exact-uuid','goal_id':'goal-exact-uuid','status':'completed','result':{'status':'partial'}}],
            'effects':[{'id':'effect-exact-uuid','work_id':'action-exact-uuid','status':'unknown','taskId':'task-exact-uuid','request':{'timestamps':[1,5,10]}}],'complete':True}


HISTORICAL_BODY='COMPLETED_HISTORICAL_BODY_'+('large historical payload '*3000)


def snapshot_with_history():
    value=snapshot()
    value['goals'].append({'id':'closed-goal','status':'completed','title':'Ended goal despite unknown execution','source':{'originalGoal':HISTORICAL_BODY}})
    value['actions'].append({'id':'closed-action','goal_id':'goal-exact-uuid','status':'completed','result':{'summary':HISTORICAL_BODY,'unfinished':[]}})
    value['actions'].append({'id':'ended-goal-unknown-action','goal_id':'closed-goal','status':'unknown','result':{'unfinished':['Unknown execution must stay out of proactive goal input']}})
    value['effects'].append({'id':'closed-effect','work_id':'closed-action','state':'finished','completion_state':'done','args':{'body':HISTORICAL_BODY},'result':{'body':HISTORICAL_BODY}})
    return value


def assert_goal_only(case,payload,value):
    case.assertEqual(payload,necessary_goals(value))
    case.assertEqual(set(payload),{'goals'})
    for goal in payload['goals']:
        case.assertLessEqual(set(goal),{'id','title','revision','reason','success_condition','successCondition','source'})
        case.assertLessEqual(set(goal.get('source',{})),{'originalGoal','constraints','restrictions'})
    rendered=model_json(payload)
    for excluded in ('action-exact-uuid','effect-exact-uuid','closed-action','closed-effect','closed-goal','COMPLETED_HISTORICAL_BODY_','Actual finding'):
        case.assertNotIn(excluded,rendered)


def service(value,event='activation-1',scope='private:12345678'):
    return {'role':'user','content':SOURCE+model_json({'scope':scope,'sender_id':'90000000','event_id':event,'kind':'platform_service_not_user'})+'\n'+model_json({'kind':'autonomous_activation','activationId':event,'currentGoals':value})}


def candidate(journal,value,event,*,retire=False):
    messages=([{'role':'system','content':'STABLE'}] if retire or journal.last is None else copy.deepcopy(journal.last.wire))+[service(value,event)]
    journal.serial+=1
    result=Prepared(journal.serial,None,copy.deepcopy(messages),messages,{},1,1,'fixture')
    tail_service(result,journal,NS(scope='private:12345678',event_id=event))
    return result


class GoalDelta(unittest.TestCase):
    def test_exact_roundtrip_modification_removal_reorder_and_presence(self):
        old=snapshot();new=copy.deepcopy(old)
        new['goals'][0]['revision']=8;new['goals'][0]['source']['originalGoal']='Literal full constraint'
        del new['goals'][0]['progress'];new['goals'].insert(0,{'id':'second','revision':1,'title':'New'})
        new['effects'][0]['status']='failed';new['actions']=[];del new['complete']
        encoded=goal_delta(old,new,'private:12345678')
        self.assertEqual(apply_goal_delta(old,encoded,'private:12345678'),new)
        reordered=copy.deepcopy(new);reordered['goals'].reverse()
        delta=goal_delta(new,reordered,'private:12345678')
        self.assertEqual(delta['changes']['goals'],{'order':['goal-exact-uuid','second']})
        self.assertEqual(apply_goal_delta(new,delta,'private:12345678'),reordered)
        for value in ({'goals':[]},{'goals':[],'effects':[]},{'goals':[]}):
            new_delta=goal_delta(old,value,'private:12345678')
            self.assertEqual(apply_goal_delta(old,new_delta,'private:12345678'),value)
            old=value

    def test_sqlite_restart_stage_failure_unknown_submission_and_checkpoint(self):
        db=sqlite3.connect(':memory:');scope='private:12345678';value=snapshot_with_history()
        journal=ReadonlyJournal(db,scope);first=candidate(journal,value,'activation-1')
        self.assertEqual(committed_goals(first.wire,scope)[0],necessary_goals(value))
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',model_json(first.wire))
        initial=json.loads(first.wire[-1]['content'].split('\n',1)[1])['currentGoals']
        assert_goal_only(self,initial,value)
        self.assertEqual(apply_goal_delta(None,initial,scope),necessary_goals(value))
        self.assertTrue(journal.stage_candidate(first));self.assertTrue(journal.commit(first))
        before=copy.deepcopy(journal.last.wire)
        second=candidate(journal,value,'activation-2')
        self.assertEqual(second.wire[:len(before)],before)
        assert_goal_only(self,json.loads(second.wire[-1]['content'].split('\n',1)[1])['currentGoals'],value)
        changed=copy.deepcopy(value);changed['goals'][0]['revision']=8
        failed=candidate(journal,changed,'activation-failed')
        with patch.object(journal,'_persist',return_value=False):self.assertFalse(journal.stage_candidate(failed))
        restored=ReadonlyJournal(db,scope)
        retry=candidate(restored,changed,'activation-retry')
        self.assertEqual(committed_goals(retry.wire,scope)[0],necessary_goals(changed))
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',model_json(retry.wire))
        assert_goal_only(self,json.loads(retry.wire[-1]['content'].split('\n',1)[1])['currentGoals'],changed)
        self.assertEqual(committed_goals(retry.wire,scope)[0]['goals'][0]['revision'],8)
        # A durably staged request may have failed/unknown HTTP. Keep its exact
        # physical input after restart; it is not a successful model response.
        self.assertTrue(restored.stage_candidate(retry))
        unknown=ReadonlyJournal(db,scope);self.assertEqual(unknown.last.wire,retry.wire)
        following=candidate(unknown,changed,'activation-after-unknown')
        self.assertEqual(following.wire[:len(retry.wire)],retry.wire)
        assert_goal_only(self,json.loads(following.wire[-1]['content'].split('\n',1)[1])['currentGoals'],changed)
        # prepare's legal retirement can shorten the prefix below old length.
        reanchored=candidate(unknown,changed,'activation-after-checkpoint',retire=True)
        payload=json.loads(reanchored.wire[-1]['content'].split('\n',1)[1])['currentGoals']
        assert_goal_only(self,payload,changed)
        self.assertEqual(apply_goal_delta(None,payload,scope),necessary_goals(changed))
        self.assertNotIn('COMPLETED_HISTORICAL_BODY_',model_json(reanchored.wire))
        self.assertEqual(reanchored.wire[0],{'role':'system','content':'STABLE'})
        self.assertEqual(reanchored.usage,history_usage(reanchored.wire))
        db.close()

    def test_scope_authorization_and_partial_chain_retirement(self):
        value=snapshot();value['goals'].append({'id':'foreign','source':{'scope':'group:87654321'}})
        visible=visible_goals(value,'private:12345678')
        self.assertEqual([g['id'] for g in visible['goals']],['goal-exact-uuid'])
        db=sqlite3.connect(':memory:');journal=ReadonlyJournal(db,'private:12345678')
        first=candidate(journal,visible,'a');self.assertTrue(journal.commit(first))
        changed=copy.deepcopy(visible);changed['goals'][0]['revision']=9
        second=candidate(journal,changed,'b');self.assertTrue(journal.commit(second))
        third=candidate(journal,changed,'c')
        # Losing a historical activation must not affect the current goals.
        third.wire.pop(2);third.base.pop(2)
        # Build a new full current input as prepare would do.
        third.wire[-1]=service(changed,'c');third.base[-1]=copy.deepcopy(third.wire[-1])
        tail_service(third,journal,NS(scope='private:12345678',event_id='c'))
        encoded=json.loads(third.wire[-1]['content'].split('\n',1)[1])['currentGoals']
        assert_goal_only(self,encoded,changed)
        self.assertEqual(apply_goal_delta(None,encoded,'private:12345678'),necessary_goals(changed))
        other=ReadonlyJournal(db,'group:87654321');other_first=candidate(other,visible,'d')
        self.assertEqual(committed_goals(first.wire,'group:87654321'),(None,[]))
        with self.assertRaises(ValueError):apply_goal_delta(None,goal_delta(None,visible,'private:12345678'),'group:87654321')
        db.close()

    def test_35_goal_only_activations_and_immutable_tool_continuation(self):
        db=sqlite3.connect(':memory:');journal=ReadonlyJournal(db,'private:12345678');value=snapshot_with_history();a=0;b=0;rows=[]
        for number in range(1,36):
            if number==5:value['goals'][0].update(revision=8,progress='New actual progress')
            if number==10:value['goals'].append({'id':'second-goal-exact','revision':1,'title':'Second goal'})
            previous=copy.deepcopy(journal.last.wire) if journal.last else []
            prepared=candidate(journal,value,str(number))
            self.assertEqual(prepared.wire[:len(previous)],previous)
            self.assertEqual(committed_goals(prepared.wire,'private:12345678')[0],necessary_goals(value))
            assert_goal_only(self,json.loads(prepared.wire[-1]['content'].split('\n',1)[1])['currentGoals'],value)
            self.assertNotIn('COMPLETED_HISTORICAL_BODY_',model_json(prepared.wire))
            full=len(model_json(service(value,str(number))));current=len(model_json(prepared.wire[-1]));a+=full;b+=current
            if number in (1,2,5,10,20,35):rows.append({'activation':number,'full_ledger_chars':full,'current_goals_chars':current,'full_ledger_cumulative':a,'current_goals_cumulative':b})
            self.assertTrue(journal.commit(prepared))
        self.assertLess(b,a)
        frozen=copy.deepcopy(journal.last.wire);continuation=copy.deepcopy(journal.last)
        continuation.wire.extend([{'role':'assistant','content':None,'tool_calls':[{'id':'x'}]},{'role':'tool','tool_call_id':'x','content':'real result'}])
        tail_service(continuation,journal,NS(scope='private:12345678',event_id='35'))
        self.assertEqual(continuation.wire[:len(frozen)],frozen)
        print('GOAL_ONLY_GROWTH '+model_json({'unit':'unicode_characters_service_physical_message','rows':rows,'full_ledger_total':a,'current_goals_total':b,'tokens':'not_measured'}))
        db.close()

    def test_current_goals_capacity_checked_before_stage(self):
        from readonly_journal import ContextCapacityExceeded
        db=sqlite3.connect(':memory:');journal=ReadonlyJournal(db,'private:12345678')
        value=snapshot();encoded=candidate(journal,value,'activation-1')
        limit=history_usage(encoded.wire)['textChars']-1
        with patch('decision_history.MAX_RETAINED_TEXT_CHARS',limit):
            with self.assertRaises(ContextCapacityExceeded):candidate(journal,value,'activation-1')
        self.assertIsNone(journal.last)
        self.assertIsNone(ReadonlyJournal(db,'private:12345678').last)
        db.close()


if __name__=='__main__':unittest.main()

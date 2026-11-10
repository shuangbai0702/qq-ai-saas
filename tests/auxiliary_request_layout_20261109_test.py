"""Pure auxiliary views and real entry points; no paid calls or network."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from context_transport import decision_request,memory_context_view
import autonomy_test as fixture


def logical(request):
    result={}
    def merge(target, value):
        for key,item in value.items():
            if isinstance(item,dict) and isinstance(target.get(key),dict):merge(target[key],item)
            else:target[key]=item
    for message in request.get('contexts',[]):merge(result,json.loads(message['content']))
    merge(result,json.loads(request['prompt']))
    return result


def resolve(data,pointer):
    for part in pointer.lstrip('/').split('/'):
        part=part.replace('~1','/').replace('~0','~')
        data=data[int(part)] if isinstance(data,list) else data[part]
    return data


class AuxiliaryView(unittest.TestCase):
    def test_foundation_precedes_person_specific_facts_and_dynamic_changes(self):
        payload={'memory':{'facts':[{'text':'first person'}],
                   'memory_foundation':{'conversation_summaries':[{'summary':'stable history'}]}},
                 'current_time':'first'}
        first=decision_request(payload,'review',reference_first=True)
        changed=copy.deepcopy(payload);changed['memory']['facts']=[{'text':'another person'}];changed['current_time']='later'
        second=decision_request(changed,'review',reference_first=True)
        self.assertEqual(first['contexts'],second['contexts'])
        self.assertIn('stable history',first['contexts'][0]['content'])
        self.assertNotIn('facts',first['contexts'][0]['content'])
        self.assertIn('another person',second['prompt'])
        self.assertIn('later',second['prompt'])

    def summary(self):
        return {'id':3,'summary':'完整的人物关系与经历正文','source_events':['event-'+str(i) for i in range(1200)],
                'kind':'conversation_summary_not_verified_facts','source_scope':'private:12345',
                'at':123,'level':'large','protected_claims':[{'text':'不公开私聊','source_events':['claim-'+str(i) for i in range(9)]}]}
    def test_provenance_all_known_paths_is_pure_and_preserves_claims(self):
        summary=self.summary()
        for envelope in ('memory','owner_memory'):
            for outer in (None,'reference','changes'):
                memory={'memory_foundation':{'conversation_summaries':[summary]},'state':{'conversation_summaries':[summary]},
                        'cross_scope_deliveries':[{'source_events':list(summary['source_events'])}]}
                payload={envelope:memory} if outer is None else {outer:{envelope:memory}}
                before=copy.deepcopy(payload);view=logical(decision_request(payload,'duty'))
                self.assertEqual(payload,before)
                actual=(view[outer] if outer else view)[envelope]
                for root in ('memory_foundation','state'):
                    row=actual[root]['conversation_summaries'][0]
                    self.assertEqual(row['source_events'],summary['source_events'][-6:])
                    self.assertEqual(row['source_events_count'],1200)
                    self.assertEqual({k:v for k,v in row.items() if not k.startswith('source_events')},
                                     {k:v for k,v in summary.items() if k!='source_events'})
                self.assertEqual(actual['cross_scope_deliveries'],memory['cross_scope_deliveries'])
                self.assertEqual(logical(decision_request(view,'duty')),view)
        self.assertEqual(memory_context_view({'memory_foundation':{'conversation_summaries':[summary]}})['memory_foundation']['conversation_summaries'][0]['source_events_count'],1200)
    def test_short_unknown_or_malformed_provenance_is_unchanged(self):
        for values,kind in [(['one']*6,'conversation_summary_not_verified_facts'),([1]*8,'conversation_summary_not_verified_facts'),(['one']*8,'tool_receipt')]:
            row={**self.summary(),'source_events':values,'kind':kind}
            payload={'memory':{'memory_foundation':{'conversation_summaries':[row]}}}
            self.assertEqual(logical(decision_request(payload,'duty')),payload)
    def test_reference_prefix_stable_dynamic_fresh_and_system_unchanged(self):
        payload={'persona':'persona','owner_id':'12345','scope':'private:12345',
                 'memory':{'memory_foundation':{'conversation_summaries':[self.summary()]},'facts':[{'value':'tea'}],
                           'active_agreements':[{'id':1,'version':1}]},'current_time':'one','companion':{'mood':'one'}}
        first=decision_request(payload,'duty',reference_first=True)
        changed=copy.deepcopy(payload);changed.update(current_time='two',companion={'mood':'two'})
        changed['memory']['active_agreements'][0]['version']=2
        second=decision_request(changed,'duty',reference_first=True)
        a,b=logical(first),logical(second)
        self.assertEqual(list(a),['reference','context','changes'])
        self.assertEqual(a['reference'],b['reference'])
        self.assertEqual(first['contexts'],second['contexts'])
        self.assertEqual(b['changes']['memory']['active_agreements'][0]['version'],2)
        self.assertEqual(b['changes']['current_time'],'two')
        self.assertEqual(first['system_prompt'],decision_request(payload,'duty')['system_prompt'])
        changed['memory']['facts'][0]['value']='new fact'
        self.assertEqual(first['contexts'],decision_request(changed,'duty',reference_first=True)['contexts'])
        self.assertEqual(logical(decision_request(changed,'duty',reference_first=True))['changes']['memory']['facts'][0]['value'],'new fact')
    def test_owner_windows_reference_exact_events_and_preserve_metadata(self):
        row={'source_scope':'private:12345','source_event':'one','sender_id':'12345','text':'真实长正文。'*80,'at':42}
        payload={'scope':'private:12345','owner_memory':{'recent_private':[row],'recent_uncompressed':[{**row,'kind':'uncompressed','attention':'[important]'}]}}
        before=copy.deepcopy(payload);view=json.loads(decision_request(payload,'duty',reference_first=True)['prompt'])
        other=view['changes']['owner_memory']['recent_uncompressed'][0]
        self.assertEqual(resolve(view,other['text_ref']),row['text'])
        self.assertEqual(other['attention'],'[important]');self.assertEqual(other['at'],42)
        self.assertEqual(payload,before)
        for delta in ({'source_scope':'private:99999'},{'sender_id':'99999'},{'source_event':'two'},{'text':row['text'][:100]},
                      {'images':['different']},{'at':43},{'source_event':None},{'event_id':'contradiction'},{'role':'self'}):
            altered=copy.deepcopy(payload);altered['owner_memory']['recent_uncompressed'][0].update(delta)
            actual=json.loads(decision_request(altered,'duty',reference_first=True)['prompt'])['changes']['owner_memory']['recent_uncompressed'][0]
            self.assertIn('text',actual);self.assertNotIn('text_ref',actual)
    def test_receipt_reference_stays_direct_after_layout(self):
        row={'source_scope':'group:1','source_event':'one','sender_id':'12345','text':'长正文。'*100}
        payload={'scope':'group:1','messages':[row],'memory':{'recent_cross_session_messages':[row],
                 'cross_scope_deliveries':[{'confirmed_text_ref':['recent_cross_session_messages',0,'text']}]}}
        view=json.loads(decision_request(payload,'duty',reference_first=True)['prompt'])
        ref=view['changes']['memory']['cross_scope_deliveries'][0]['confirmed_text_ref']
        self.assertEqual(resolve(view,ref),row['text'])
        self.assertIn('text',view['changes']['memory']['recent_cross_session_messages'][0])
        payload['memory'].pop('cross_scope_deliveries')
        view=json.loads(decision_request(payload,'duty',reference_first=True)['prompt'])
        self.assertEqual(view['changes']['messages'][0]['text'],row['text'])
        self.assertEqual(view['changes']['memory']['recent_cross_session_messages'][0]['text'],row['text'])


class AuxiliaryEntries(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.AutonomyTest.setUp
    tearDown=fixture.AutonomyTest.tearDown

    async def test_presence_preserves_observations(self):
        scope='group:12345';row={'source_event':'same','source_scope':scope,'sender_id':'22222222','text':'正文。'*150,'images':['one']}
        timeline=[{**row,'text':row['text'][:100]},{**row,'source_scope':'group:67890'},{**row,'images':['two']},row]
        with patch('autonomy.time.time',return_value=self.now):
            result=await self.store.presence.evaluate(scope,self.cfg,'same',self.provider,[row],{},
                         {'timeline':timeline},lambda:True,self.metrics,self.event,self.now)
        self.assertTrue(result[0].reply)
        view=logical(self.provider.calls[0]);attention=view['changes']['attention']['timeline']
        self.assertEqual(len(attention),4)
        self.assertTrue(all('text' in item for item in attention)) # full row differs from 300-char incoming truncation
        self.assertEqual(view['changes']['messages'][0]['images'],['one'])
        self.assertEqual(list(view)[:3],['reference','context','changes'])

    async def test_plan_supplies_trusted_event_and_scope(self):
        self.cfg['behavior'].update(ownerContactEnabled=False,proactiveModelSchedule=True,proactiveTargets=['private:22222222'],
                                    silenceSeconds=300,proactiveIntervalSeconds=300,memory=True)
        import initiative
        initiative.incoming(self.db,'private:22222222','22222222','99999999','共同话题',self.now-400)
        self.db.execute('INSERT INTO memory_pending VALUES(?,?,?,?,?,?)',('private:22222222','22222222','trusted-event','共同话题','',self.now-400));self.db.commit()
        provider=fixture.Provider({'contact':False,'delay_minutes':30,'topic':''})
        with patch('companion_schedule.time.time',return_value=self.now):
            self.store.planner.maybe_start(self.cfg,provider,lambda:True,self.event,self.metrics,self.now)
            await self.store.planner.task
        row=json.loads(provider.calls[0]['prompt'])['changes']['recent_received'][0]
        self.assertEqual(row['source_event'],'trusted-event');self.assertEqual(row['source_scope'],'private:22222222')
        self.assertEqual(row['sender_id'],'22222222');self.assertEqual(row['at'],self.now-400)


if __name__=='__main__':unittest.main()

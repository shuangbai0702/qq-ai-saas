import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from context_protocol import model_json,READONLY_CURRENT
from memory_model_view import FORMAT,memory_view,memory_json
from context_transport import decision_request,request_context_view
from context_foundation import FOUNDATION_CURRENT
from readonly_journal import ReadonlyJournal
from decision_history import apply_changes


def expand(value):
    if isinstance(value,dict):
        if value.get('format')==FORMAT:
            return [{**copy.deepcopy(value['common']),**dict(zip(value['columns'],row))} for row in value['rows']]
        return {k:expand(v) for k,v in value.items()}
    if isinstance(value,list):return [expand(v) for v in value]
    return value


def fixture():
    return {
        'relations':[{'source_scope':'private:12345678','subject':str(12345678+i),'affinity':i,'emotion':'平静','emotion_expires':0,'observed_at':100+i,'trust':.8,'impression':'愿意交流，仍需按当前边界判断。','semantic_updated':90} for i in range(20)],
        'facts':[{'name':'饮料'+str(i),'value':'喜欢红茶，不加糖。','source_event':'real-'+str(i),'version':i+1,'remembered_at':100+i,'kind':'stable','origin':'explicit','source_scope':'private:12345678','subject':'12345678','recall_reason':'checkpoint_foundation'} for i in range(20)],
        'protected_claims':[{'id':'claim-'+str(i),'text':'约定待对方确认后再联系，不代表已经发送。','kind':'agreement','speaker':'user','about':'约定'+str(i),'occurredAt':100+i,'status':['open','unknown','superseded','canceled'][i%4],'visibility':'private:12345678','stage':['proposed','unknown','executed','canceled'][i%4],'due':'明天18:00','sourceRefs':[{'scope':'private:12345678','role':'user'}]} for i in range(20)]}


def messages(state,tail):
    system,history=request_context_view('fixed',tail,'new','policy','rules',model_json(state))
    return [{'role':'system','content':system}]+history


class MemoryModelView(unittest.TestCase):
    def test_all_fields_types_order_and_source_objects_preserved(self):
        raw=fixture();before=copy.deepcopy(raw);view=memory_view(raw)
        for key in raw:self.assertEqual(view[key]['format'],FORMAT)
        self.assertEqual(expand(view),raw);self.assertEqual(raw,before)
        self.assertEqual([x['id'] for x in expand(view)['protected_claims']],[x['id'] for x in raw['protected_claims']])

    def test_small_heterogeneous_null_unknown_and_tool_payloads(self):
        cases=[[],[{'value':None}],[{'a':1},{'b':None}],['raw'],[{'a':1},{'a':1.0}]]
        for rows in cases:
            raw={'facts':rows};self.assertEqual(expand(memory_view(raw)),raw)
        raw={'tool_results':fixture(),'authority':fixture(),'receipt':fixture(),'text':model_json(fixture())}
        self.assertEqual(memory_view(raw),raw)
        rows=[dict(row,future={'null':None,'flags':[True,False]}) for row in fixture()['facts']]
        self.assertEqual(expand(memory_view({'facts':rows})),{'facts':rows})

    def test_stateless_final_request_and_summary_snapshot(self):
        raw={'persona':'fixed','owner_id':'12345678','scope':'group:23456789','memory':{'memory_foundation':fixture(),'facts':fixture()['facts']}}
        request=decision_request(raw,'judge',reference_first=True)
        supplied=[json.loads(row['content']) for row in request['contexts']]+[json.loads(request['prompt'])]
        self.assertIn(FORMAT,model_json(supplied))
        reference=expand(supplied[0]) if 'memory_foundation' in model_json(supplied[0]) else expand(supplied[1])
        self.assertEqual(reference['reference']['memory']['memory_foundation'],fixture())
        snapshot={'existing_memory':fixture(),'previous_commitments':fixture()['protected_claims'],'turns':[{'text':'unchanged'}]}
        self.assertEqual(expand(json.loads(memory_json(snapshot))),snapshot)

    def test_journal_new_wire_raw_state_delta_and_committed_prefix(self):
        raw={'memory_foundation':{'conversation_summaries':[],**fixture()},'state':{'protected_claims':fixture()['protected_claims']}}
        journal=ReadonlyJournal();tail=[{'role':'user','content':'first'}]
        first=journal.prepare(messages(raw,tail),'stamp');prefix=copy.deepcopy(first.wire)
        self.assertIn(FORMAT,model_json(prefix));self.assertEqual(first.state,raw)
        self.assertTrue(journal.commit(first))
        newer=copy.deepcopy(raw);newer['state']['protected_claims'][0]['status']='canceled'
        pending=journal.prepare(messages(newer,tail+[{'role':'assistant','content':'reply'},{'role':'user','content':'next'}]),'stamp')
        self.assertEqual(pending.wire[:len(prefix)],prefix)
        state=copy.deepcopy(raw);state.pop('memory_foundation')
        for message in pending.wire[len(prefix):]:
            if str(message.get('content','')).startswith(READONLY_CURRENT+'\n'):
                packet=expand(json.loads(message['content'].split('\n',1)[1]))
                state=apply_changes(state,packet['readonlyChanges'])
        self.assertEqual(state,{'state':newer['state']})
        self.assertEqual(pending.state,newer)

    def test_core_note_event_projection_keeps_authority_snapshot_raw(self):
        from context_transport import EVENT_CONTEXT_CURRENT
        raw={'state':{},'persona_core_note':{'text':'note','protected_claims':fixture()['protected_claims']},
             'identity':{'sender_id':'12345678','owner':False,'scope':'private:12345678'}}
        journal=ReadonlyJournal();tail=[{'role':'user','content':'first'}]
        first=journal.prepare(messages(raw,tail),'stamp')
        self.assertTrue(journal.commit(first));prefix=copy.deepcopy(first.wire)
        event=next(m for m in first.wire if str(m.get('content','')).startswith(EVENT_CONTEXT_CURRENT+'\n'))
        self.assertIn(FORMAT,event['content'])
        self.assertNotIn(FORMAT,journal.last.authority['content'])
        self.assertEqual(expand(json.loads(event['content'].split('\n',1)[1])),json.loads(first.authority['content'].split('\n',1)[1]))
        newer=copy.deepcopy(raw);newer['persona_core_note']['protected_claims'][0]['status']='canceled'
        pending=journal.prepare(messages(newer,tail+[{'role':'user','content':'next'}]),'stamp')
        self.assertEqual(pending.wire[:len(prefix)],prefix)
        self.assertNotIn(FORMAT,pending.authority['content'])


class SummaryProviderWire(unittest.IsolatedAsyncioTestCase):
    async def test_real_summary_sdk_http_contains_lossless_table(self):
        import semantic_provider_wire_test as wire_fixture
        helper=wire_fixture.SemanticProviderWire('test_summary_http_then_checkpoint_retires_originals_before_next_http')
        await helper.asyncSetUp()
        self.addAsyncCleanup(helper.asyncTearDown)
        for i in range(20):
            helper.store.memory.remember(wire_fixture.SCOPE,'12345678','资料'+str(i),'原始完整事实'+str(i),'source-'+str(i),100+i)
        helper.store.db.commit()
        helper.populate()
        from companion_insights import snapshot
        prior=snapshot(helper.store.db,wire_fixture.SCOPE,{'12345678'},helper.cfg)['memory_state']
        await helper.summarize()
        helper.assert_complete_summary_http()
        prompts=[]
        for message in helper.requests[0]['messages']:
            parts=message['content'] if isinstance(message['content'],list) else [{'text':message['content']}]
            for part in parts:
                try:value=json.loads(part.get('text',''))
                except (ValueError,TypeError):continue
                if isinstance(value,dict) and 'existing_memory' in value:prompts.append(value)
        self.assertEqual(len(prompts),1)
        self.assertEqual(prompts[0]['existing_memory']['facts']['format'],FORMAT)
        self.assertEqual(expand(prompts[0]['existing_memory']),prior)


if __name__=='__main__':unittest.main()

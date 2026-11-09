import copy,json,sys,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
from context_transport import decision_context_view
from group_freshness import review_request


def resolve(document,pointer):
    value=document
    for key in pointer.lstrip('/').split('/'):
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


def restore(document):
    result=copy.deepcopy(document)
    def walk(value):
        if isinstance(value,list):
            for item in value:walk(item)
        elif isinstance(value,dict):
            for field in ('text','confirmed_text'):
                pointer=value.pop(field+'_ref',None)
                if pointer is not None:value[field]=resolve(document,pointer)
            for item in list(value.values()):walk(item)
    walk(result)
    result.pop('reference_guidance',None)
    return result


class FreshnessProjection(unittest.TestCase):
    def row(self,**changes):
        return {'source_scope':'group:55555','source_event':'1','sender_id':'12345',
                'role':'member','text':'完整且足够长的原话与细节。'*35,**changes}
    def request(self,payload):return review_request(payload,'固定人格','12345','99999')
    def payload(self):
        row=self.row()
        return {'scope':'group:55555','conversation':{'participants':[{'id':'12345'}],'timeline':[row]},
                'memory':{'facts':[{'text':'当前事实','revision':2}],
                          'memory_foundation':{'conversation_summaries':[{'text':'完整摘要'}]},
                          'active_agreements':[{'version':3,'text':'适用约定'}],
                          'recent_cross_session_messages':[copy.deepcopy(row)]},
                'original_message':copy.deepcopy(row),'latest_messages':[copy.deepcopy(row)],
                'draft':row['text'],'already_sent':row['text'],'current_time':'2026-11-09T10:00:00+08:00'}
    def test_lossless_attributed_projection_and_business_roles(self):
        payload=self.payload();before=copy.deepcopy(payload)
        request=self.request(payload);view=json.loads(request['prompt']);plain=restore(view)
        self.assertEqual(payload,before)
        self.assertEqual(list(view)[:4],['reference','context','changes','review'])
        self.assertEqual(plain['context']['conversation'],payload['conversation'])
        self.assertEqual(plain['review']['latest_messages'],payload['latest_messages'])
        self.assertEqual(plain['review']['original_message'],payload['original_message'])
        self.assertEqual(plain['review']['draft'],payload['draft'])
        self.assertEqual(plain['review']['already_sent'],payload['already_sent'])
        self.assertEqual({**plain['reference']['memory'],**plain['changes']['memory']},payload['memory'])
        self.assertIn('text_ref',view['review']['original_message'])
        # Both output business roles remain full bodies, even with equal text.
        self.assertEqual(request['prompt'].count(payload['draft']),3)
    def test_conflicts_never_share_body(self):
        cases=[{'source_scope':'private:12345'},{'source_event':'2'},{'sender_id':'54321'},
               {'role':'self'},{'role':'user'},{'text':'不同正文'*80},
               {'at':200},{'reply_to':{'event_id':'other'}},{'mentions':['54321']},
               {'sender_name':'另一个名字'},{'delivery':'unknown'},
               {'attachments':[{'image':'changed'}]},{'future':{'revision':2}}]
        original=self.row(at=100,reply_to={'event_id':'quoted'},mentions=['12345'],
                          sender_name='原名字',delivery='confirmed',attachments=[{'image':'original'}],future={'revision':1})
        for change in cases:
            with self.subTest(change=change):
                payload={'scope':'group:55555','conversation':{'timeline':[original]},
                         'latest_messages':[{**original,**change}]}
                row=json.loads(self.request(payload)['prompt'])['review']['latest_messages'][0]
                self.assertIn('text',row);self.assertNotIn('text_ref',row)
    def test_complementary_metadata_is_retained_but_conflicts_not_transitive(self):
        bare=self.row();first=self.row(mentions=['11111']);second=self.row(mentions=['22222'])
        view=decision_context_view({'scope':'group:55555','messages':[bare,first,second]})
        self.assertIn('text_ref',view['messages'][1]);self.assertIn('text',view['messages'][2])
        self.assertEqual(restore(view)['messages'],[bare,first,second])
    def test_media_presence_and_identity_alias_conflict_are_not_assumed_equal(self):
        for changed in (self.row(attachments=[]),self.row(event='different'),self.row(scope='private:12345')):
            view=decision_context_view({'scope':'group:55555','messages':[self.row(),changed]})
            self.assertIn('text',view['messages'][1])
    def test_existing_receipt_reference_relocated_without_dangling_or_role_merge(self):
        payload=self.payload();payload['memory']['cross_scope_deliveries']=[
            {'source_scope':'group:55555','source_event':'1','delivery':'confirmed','recipient_id':'12345',
             'confirmed_text_ref':['recent_cross_session_messages',0,'text'],'text_complete':True}]
        view=json.loads(self.request(payload)['prompt'])
        receipt=view['changes']['memory']['cross_scope_deliveries'][0]
        self.assertEqual(receipt['confirmed_text_ref'],'/changes/memory/recent_cross_session_messages/0/text')
        self.assertEqual(resolve(view,receipt['confirmed_text_ref']),self.row()['text'])
        self.assertEqual(self.request(payload)['prompt'].count(self.row()['text']),3)
        self.assertEqual(restore(view)['changes']['memory']['cross_scope_deliveries'][0]['delivery'],'confirmed')
    def test_stable_reference_prefix_and_current_revision(self):
        payload=self.payload();before=self.request(payload)
        payload['draft']='更新的草稿';payload['current_time']='2026-11-09T10:00:01+08:00'
        after=self.request(payload)
        self.assertEqual(before['system_prompt'],after['system_prompt'])
        self.assertEqual(before['prompt'].split(',"review":')[0],after['prompt'].split(',"review":')[0])
        payload['memory']['facts'][0]['revision']=3
        self.assertNotEqual(json.loads(after['prompt'])['reference'],json.loads(self.request(payload)['prompt'])['reference'])
    def test_equivalent_object_order_is_deterministic_without_sorting_lists(self):
        payload=self.payload();payload['future']={'b':2,'a':1}
        other={key:payload[key] for key in reversed(payload)}
        other['memory']={key:payload['memory'][key] for key in reversed(payload['memory'])}
        other['future']={'a':1,'b':2}
        self.assertEqual(self.request(payload),self.request(other))
        payload['latest_messages']=[self.row(source_event='2'),self.row(source_event='1')]
        self.assertEqual([r['source_event'] for r in json.loads(self.request(payload)['prompt'])['review']['latest_messages']],['2','1'])

if __name__=='__main__':unittest.main()

"""Relationship identity, request event conservation, and stable prefix contract."""
import copy,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import relationships
from decision_events import project
from context_transport import decision_request


class Relationships(unittest.TestCase):
    def test_atomic_merge_backup_and_global_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'behavior.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE relations(scope TEXT,subject TEXT,affinity INTEGER,emotion TEXT,expires REAL,last_seen REAL,trust REAL,impression TEXT,semantic_updated REAL,PRIMARY KEY(scope,subject))')
                rows=[('private:10001','10001',4,'平静',0,200,.1,'旧印象',10),
                      ('group:20001','10001',8,'平静',0,100,.5,'当前综合印象',20)]
                db.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?)',rows)
                db.set_authorizer(lambda action,name,*args:sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_DROP_TABLE and name=='relations_migrating' else sqlite3.SQLITE_OK)
                with self.assertRaises(sqlite3.DatabaseError):relationships.migrate(db)
                db.set_authorizer(None)
                self.assertEqual(db.execute('SELECT * FROM relations').fetchall(),rows)
                # A failed transaction leaves a valid rollback artifact; retry
                # verifies and reuses it rather than blocking forever.
                result=relationships.migrate(db)
                self.assertEqual(result['after'],1)
                current=relationships.state(db,'10001')
                self.assertEqual((current['affinity'],current['impression'],current['last_seen']),(8,'当前综合印象',200))
                with self.assertRaises(sqlite3.IntegrityError):db.execute('INSERT INTO relations(scope,subject,affinity,emotion,expires,last_seen,trust,impression,semantic_updated) VALUES(?,?,?,?,?,?,?,?,?)',rows[0])
                self.assertIsNone(relationships.migrate(db))
            with closing(sqlite3.connect(str(path)+'.relations-before-global.sqlite')) as saved:
                self.assertEqual(saved.execute('SELECT * FROM relations').fetchall(),rows)

    def test_six_distinct_speakers_do_not_filter_messages(self):
        with closing(sqlite3.connect(':memory:')) as db:
            rows=[{'source_scope':'group:20001','source_event':str(i),'sender_id':str(10000+i),'at':i} for i in range(1,9)]
            rows += [dict(rows[-1],source_event='again',at=20)]
            before=copy.deepcopy(rows)
            selected=relationships.latest_speakers(db,'group:20001',rows)
            self.assertEqual(selected,['10008','10007','10006','10005','10004','10003'])
            self.assertEqual(relationships.latest_speakers(db,'group:20001',rows,self_id='10008'),['10007','10006','10005','10004','10003','10002'])
            self.assertEqual(rows,before)
            self.assertEqual(relationships.latest_speakers(db,'group:other',rows),[])

    def test_speaker_order_uses_protocol_not_relationship_clock(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE pipeline_work_inputs(scope,event,payload)')
            db.execute('INSERT INTO pipeline_work_inputs VALUES(?,?,?)',('group:20001','e',json.dumps({'message_id':'e','user_id':10001,'time':1})))
            rows=[{'source_event':'e','sender_id':'10001','at':999},
                  {'source_event':'f','sender_id':'10002','at':2}]
            self.assertEqual(relationships.latest_speakers(db,'group:20001',rows),['10002','10001'])


class Events(unittest.TestCase):
    def message(self,event='e',at=10,text='完整的真实消息',**extra):
        return {'source_scope':'group:20001','source_event':event,'sender_id':'10001','at':at,'text':text,**extra}

    def test_identity_conservation_truncation_time_media_and_distinct_events(self):
        first=self.message(at=2,media=[{'type':'image','id':'pic'}])
        value={'scope':'group:20001','messages':[dict(first,text='完整'),self.message('other',1)],
               'attention':{'timeline':[first]},'memory':{'recent_cross_session_messages':[self.message('third',3)]}}
        original=copy.deepcopy(value);result=project(value)
        self.assertEqual(value,original)
        self.assertEqual([r['source_event'] for r in result['messages']],['other','e','third'])
        self.assertEqual(result['messages'][1]['text'],'完整的真实消息')
        self.assertEqual(result['messages'][1]['media'],first['media'])
        self.assertNotIn('timeline',result['attention'])
        self.assertNotIn('recent_cross_session_messages',result['memory'])

    def test_author_conflict_is_not_merged_and_unproven_is_not_dropped(self):
        with self.assertRaisesRegex(ValueError,'author_conflict'):
            project({'messages':[self.message(),self.message(sender_id='10002')]})
        result=project({'messages':[{'text':'相同原文'},{'text':'相同原文'}]})
        self.assertEqual(len(result['messages']),2)
        self.assertTrue(all(r['identity_status']=='unproven' for r in result['messages']))

    def test_draft_confirmation_partial_unknown_and_multiple_bubbles(self):
        draft={'role':'self','source_scope':'group:20001','source_event':'work','delivery':'model_output_not_send_receipt','text':'第一句第二句'}
        receipt={**draft,'delivery':'partial','confirmed_text':'第一句','message_ids':['bubble-1','bubble-2']};receipt.pop('text')
        result=project({'messages':[draft],'memory':{'cross_scope_deliveries':[receipt]}})['messages']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['delivery'],'partial')
        self.assertEqual(result[0]['text'],'第一句')
        self.assertEqual(result[0]['unconfirmed_suffix'],'第二句')
        self.assertEqual(result[0]['message_ids'],['bubble-1','bubble-2'])
        unknown=project({'messages':[dict(draft,delivery='unknown')]})['messages'][0]
        self.assertEqual(unknown['delivery'],'unknown')

    def test_quote_has_one_target_and_does_not_relabel_inbound(self):
        value={'messages':[self.message('one',reply_to={'event_id':'target','sender_id':'10001','text':'引用正文'}),
                           self.message('two',reply_to={'event_id':'target','sender_id':'10001','text':'引用正文'})]}
        rows=project(value)['messages']
        self.assertEqual(len([r for r in rows if r['role']=='quoted']),1)
        self.assertTrue(all('text' not in r['reply_to'] for r in rows if 'reply_to' in r))

    def test_prefix_is_identical_with_dynamic_event_changes(self):
        payload={'persona':'固定人格','owner_id':'10001','scope':'group:20001',
                 'memory':{'cross_scope_guidance':'固定边界','memory_foundation':{'summary':'合法基础'}},'messages':[self.message()]}
        a=decision_request(payload,'固定职责',reference_first=True)
        payload['messages'].append(self.message('other',20))
        b=decision_request(payload,'固定职责',reference_first=True)
        self.assertEqual(a['system_prompt'],b['system_prompt'])
        self.assertEqual(a['contexts'],b['contexts'])
        self.assertEqual([r['role'] for r in a['contexts']],['user','user'])
        self.assertEqual(len(json.loads(b['prompt'])['changes']['messages']),2)

    def test_preformatted_freshness_review_uses_same_event_owner(self):
        value={'reference':{'memory':{'memory_foundation':{'summary':'基础'}}},'context':{'scope':'group:20001','conversation':{'timeline':[self.message()]}},
               'changes':{'memory':{}},'review':{'original_message':self.message(text='完整'),'latest_messages':[self.message()]}}
        result=project(value)
        self.assertEqual(len(result['changes']['messages']),1)
        self.assertEqual(result['review'],{})


class Agreements(unittest.TestCase):
    def test_auxiliary_filter_preserves_authority_cas_and_immediate_revision(self):
        from behavior import BehaviorStore
        from bot_pipeline import decision_memory
        from agreement_state import apply_update
        with tempfile.TemporaryDirectory() as folder:
            store=BehaviorStore(Path(folder)/'bot.sqlite');db=store.db
            try:
                cfg={'memory':True,'ownerUin':'10001'};scope='private:10001';people=['10001','bot']
                def update(action,topic,event,speaker='10001',revision=None,**kw):
                    return apply_update(db,action,topic,speaker,people,scope,event,cfg=cfg,expected_revision=revision,**kw)
                for action in ('cancel','expire','fulfill'):
                    row,error=update('propose','事项'+action,action)
                    self.assertEqual(error,'')
                    row,error=update(action,'事项'+action,action,revision=1)
                    self.assertEqual(error,'')
                row,error=update('propose','有效约定','live');self.assertEqual(error,'')
                view=decision_memory(db,cfg,scope,'10001',0,recent=False)
                self.assertEqual([(r['status'],r['revision']) for r in view['active_agreements']],[('proposed',1)])
                row,error=update('accept','有效约定','live',speaker='bot',revision=1);self.assertEqual(error,'')
                self.assertEqual(decision_memory(db,cfg,scope,'10001',0,recent=False)['active_agreements'][0]['revision'],2)
                self.assertEqual(update('cancel','有效约定','live',revision=1)[1],'agreement_revision_changed')
                row,error=update('cancel','有效约定','live',revision=2);self.assertEqual(error,'')
                self.assertEqual(decision_memory(db,cfg,scope,'10001',0,recent=False)['active_agreements'],[])
                self.assertEqual(db.execute('SELECT count(*) FROM memory_agreements').fetchone()[0],4)
            finally:store.close()


if __name__=='__main__':unittest.main()

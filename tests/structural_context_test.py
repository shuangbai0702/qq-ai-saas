"""Append-only SDK recovery and lossless catalog deltas; no network/models."""
import copy,json,sys,unittest,sqlite3,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'references/astrbot'))
from context_protocol import SOURCE
from readonly_journal import window_reload_extension
from readonly_journal import ReadonlyJournal,MAX_PERSISTED_JOURNAL_BYTES
from context_protocol import READONLY_CURRENT,model_json
from decision_history import changes,apply_changes,encode
from inner_tools import tool_context_view
from unittest.mock import patch,AsyncMock


def user(event,text='question',scope='private:12345678'):
    return {'role':'user','content':SOURCE+json.dumps({'scope':scope,'sender_id':'12345678','event_id':event})+'\n'+text}
def answer(text):return {'role':'assistant','content':text}


class ReloadRecovery(unittest.TestCase):
    def setUp(self):
        self.round=[user('known'),answer('confirmed')]
        # Earlier staged user turns and an unanswered, possibly submitted tail
        # remain in the immutable journal, absent from the SDK's saved history.
        self.old=SimpleNamespace(base=[{'role':'system','content':'original'},user('missing-head')]+self.round+[user('unknown-tail')])

    def test_reload_shorter_than_baseline_keeps_unknown_submitted_tail(self):
        before=copy.deepcopy(self.old.base);fresh=user('fresh')
        self.assertEqual(window_reload_extension(self.old,self.round+[fresh]),[fresh])
        self.assertEqual(self.old.base,before)

    def test_older_known_head_and_exact_later_suffix(self):
        self.old.base.insert(1,user('older-known'))
        native=[user('retired'),answer('retired reply'),user('older-known'),answer('older saved reply')]+self.round+[user('fresh')]
        self.assertEqual(window_reload_extension(self.old,native),[user('fresh')])

    def test_staged_tail_present_is_not_replayed(self):
        fresh=user('fresh');native=self.round+[user('unknown-tail'),fresh]
        self.assertEqual(window_reload_extension(self.old,native),[fresh])

    def test_known_reorder_duplicate_or_changed_content_is_refused(self):
        self.old.base.insert(1,user('older-known'))
        for native in (self.round+[user('older-known'),user('fresh')],
                       self.round+[user('known'),user('fresh')],
                       [user('known','changed'),answer('confirmed'),user('fresh')]):
            self.assertIsNone(window_reload_extension(self.old,native))

    def test_missing_assistant_or_tool_result_cannot_be_skipped(self):
        self.old.base += [answer('not saved by SDK')]
        self.assertIsNone(window_reload_extension(self.old,self.round+[user('fresh')]))

    def test_plain_assistant_match_without_source_anchor_is_refused(self):
        self.assertIsNone(window_reload_extension(self.old,[answer('confirmed'),user('fresh')]))


class CatalogDeltas(unittest.TestCase):
    def catalog(self,start,end):
        rows=[{'handle':'qr_%020d'%i,'sender_id':'12345678','preview':'body '*35,'at':float(i)} for i in range(end,start-1,-1)]
        return {'quote_reply':rows,'qq_favorites':{'media':[], 'source_ids':['current']},'unknown':{'preserve':True}}

    def test_sliding_newest_first_catalog_is_lossless_and_bounded_delta(self):
        before=self.catalog(1,12);after=self.catalog(2,13);original=copy.deepcopy(after)
        old=tool_context_view(before);new=tool_context_view(after)
        self.assertEqual(sorted(new['quote_reply'],key=lambda r:r['at'],reverse=True),after['quote_reply'])
        ops=changes(old,new)
        self.assertEqual(apply_changes(old,ops),new)
        self.assertEqual(after,original)
        self.assertLess(len(encode(ops)),len(encode(changes(before,after)))/3)
        self.assertEqual(new['unknown'],after['unknown'])

    def test_catalog_unknown_shape_is_not_discarded(self):
        value={'quote_reply':[{'handle':'future','preview':'keep'}], 'qq_favorites':{'media':[{'future':True}], 'source_ids':[None,'current']}}
        self.assertEqual(tool_context_view(value),value)


class DurableCapacity(unittest.TestCase):
    def test_snapshot_duplicate_views_use_storage_bytes_not_wire_char_limit(self):
        db=sqlite3.connect(':memory:')
        try:
            j=ReadonlyJournal(db,'private:12345678')
            m=user('image');m['content']=[{'type':'text','text':m['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+'A'*1300000}}]
            p=j.prepare([m,{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'recent_messages':[]})}],1)
            self.assertTrue(j.stage_candidate(p))
            size=db.execute('SELECT length(cast(payload AS BLOB)) FROM fishbot_readonly_journals').fetchone()[0]
            self.assertGreater(size,2*1024*1024+256000)
            self.assertLess(size,MAX_PERSISTED_JOURNAL_BYTES)
            restored=ReadonlyJournal(db,'private:12345678')
            self.assertIsNone(restored.recovery_error)
            self.assertEqual(restored.last.wire,p.wire)
        finally:db.close()


class RecentMediaReuse(unittest.IsolatedAsyncioTestCase):
    async def test_only_exact_observed_event_with_native_image_skips_attachment(self):
        import incoming_media,quote_reply
        from astrbot.core.message.components import Plain,Image
        db=sqlite3.connect(':memory:')
        try:
            event=SimpleNamespace(get_messages=lambda:[Plain('follow-up')],get_message_str=lambda:'follow-up',get_extra=lambda k,d=None:d,
                get_sender_id=lambda:'12345678',get_self_id=lambda:'99999999',get_group_id=lambda:'',message_obj=SimpleNamespace(message_id='next'))
            row={'handle':'qm_real','sender_id':'12345678','message_id':'photo','at':time.time()}
            m=user('photo');m['content']=[{'type':'text','text':m['content']},{'type':'image_url','image_url':{'url':'data:image/png;base64,YQ=='}}]
            journal=SimpleNamespace(last=SimpleNamespace(base=[m]),recovery_error=None,retired_reason='')
            resolve=AsyncMock(side_effect=ValueError('fixture'))
            with patch.object(quote_reply,'media_catalog',return_value=[row]),patch.object(quote_reply,'resolve_media',resolve):
                await incoming_media.attach_recent(event,db,{},lambda:True,journal=journal)
                resolve.assert_not_awaited()
                # Same bytes from another event, scope or author are not proof.
                other_author={'role':'user','content':SOURCE+json.dumps({'scope':'private:12345678','sender_id':'87654321','event_id':'photo'})+'\nquestion'}
                for changed in (user('another'),user('photo',scope='group:12345678'),other_author):
                    changed['content']=[{'type':'text','text':changed['content']},m['content'][1]]
                    journal.last.base=[changed]
                    await incoming_media.attach_recent(event,db,{},lambda:True,journal=journal)
                self.assertEqual(resolve.await_count,3)
                # A retired view may be rebuilt; it cannot suppress fresh media.
                journal.last.base=[m];journal.retired_reason='retired'
                await incoming_media.attach_recent(event,db,{},lambda:True,journal=journal)
                self.assertEqual(resolve.await_count,4)
        finally:db.close()

if __name__=='__main__':unittest.main()

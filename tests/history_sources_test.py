"""Live private/group history, image identity, retirement and immutable storage."""
import asyncio,base64,copy,io,json,sqlite3,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from context_protocol import event_message,READONLY_CURRENT,model_json
from readonly_journal import ReadonlyJournal
import history_sources as history


def seed(db,messages,scope='bot_shared'):
    journal=ReadonlyJournal(db,scope)
    prepared=journal.prepare(messages+[{'role':'user','content':READONLY_CURRENT+'\n'+model_json({'state':{}})}],'fixture',now=1)
    if not journal.commit(prepared):raise AssertionError('fixture Journal not committed')
    return journal


def png(color='red'):
    from PIL import Image
    value=io.BytesIO();Image.new('RGB',(3,2),color).save(value,'PNG');return value.getvalue()


class HistorySources(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.scope='private:12345';self.raw=png();self.image='data:image/png;base64,'+base64.b64encode(self.raw).decode()
        self.work=NS(scope=self.scope,subject='12345',event_id='103')
        self.extra={'_hosted_pipeline':self.work,'_hosted_journal_scope':'bot_shared'}
        self.event=NS(get_extra=lambda k,d=None:self.extra.get(k,d),get_group_id=lambda:'',get_sender_id=lambda:'12345',get_self_id=lambda:'99999',bot=NS())
        self.settings={'botId':'bot','tenantId':'tenant','generation':1,'revision':1}
        self.native={'message_id':100,'sender':{'user_id':12345},'message':[{'type':'image','data':{'url':'https://qpic.cn/original'}}]}
        self.event.bot.get_msg=AsyncMock(return_value=self.native)
        self.messages=[event_message({'scope':self.scope,'sender_id':'12345','event_id':'100','at':'2026-11-20T00:00:00Z'},
                         [{'type':'text','text':'澪的照片'}, {'type':'image_url','image_url':{'url':self.image}}]),
                       {'role':'assistant','content':'看到了，是澪。'},
                       event_message({'scope':'private:67890','sender_id':'67890','event_id':'other'},'FOREIGN_PRIVATE'),
                       {'role':'assistant','content':'FOREIGN_REPLY'},
                       event_message({'scope':self.scope,'sender_id':'12345','event_id':'103','at':'2026-11-25T00:00:00Z'},'把之前那张发空间')]
        self.journal=seed(self.db,self.messages)

    def selected(self):return history.search(self.settings,self.event,self.db,images_only=True)
    async def data(self,reference=None):return json.loads((await history.materialize(self.settings,self.event,self.db,reference or self.selected(),lambda:True))['text'])
    def snapshot(self):return list(self.db.iterdump())

    async def test_private_query_keeps_author_time_text_reply_and_real_model_image_without_writes(self):
        before=self.snapshot();reference=self.selected();item=(await self.data(reference))['entries'][0]
        self.assertEqual(item['event_id'],'100');self.assertEqual(item['sender_id'],'12345')
        self.assertNotIn('澪的照片',json.dumps(reference));self.assertNotIn('event_id',json.dumps(reference));self.assertNotIn('base64',json.dumps(reference))
        view=await history.materialize(self.settings,self.event,self.db,reference,lambda:True)
        self.assertIn('澪的照片',view['text']);self.assertIn('看到了',view['text']);self.assertIn('2026-11-20',view['text'])
        self.assertNotIn('FOREIGN',view['text'])
        handle=item['images'][0]['source_id'];image_ref=history.image_reference(self.settings,self.event,self.db,handle)
        actual=await history.materialize(self.settings,self.event,self.db,image_ref,lambda:True)
        self.assertEqual(base64.b64decode(actual['images'][0]['data']),self.raw)
        self.assertEqual(await history.original_url(self.settings,self.event,self.db,handle,lambda:True),'https://qpic.cn/original')
        self.assertEqual(self.snapshot(),before)

    async def test_paging_filters_and_no_nearest_or_recent_fallback(self):
        page=history.search(self.settings,self.event,self.db,limit=1)
        self.assertEqual((await self.data(page))['entries'][0]['event_id'],'103');self.assertTrue(page['more'])
        older=history.search(self.settings,self.event,self.db,before=page['before'],keyword='澪',sender_id='12345')
        self.assertEqual([r['event_id'] for r in (await self.data(older))['entries']],['100'])
        for handle in ('current','recent','qm_'+'a'*20,'https://qpic.cn/original','qh_'+'0'*24):
            with self.assertRaises(ValueError):history.image_reference(self.settings,self.event,self.db,handle)
        with self.assertRaises(ValueError):history.search(self.settings,self.event,self.db,before='FOREIGN_PRIVATE')
        self.assertEqual(history.search(self.settings,self.event,self.db,keyword='absent')['refs'],[])

    async def test_scope_generation_and_native_message_identity_are_rechecked(self):
        handle=(await self.data())['entries'][0]['images'][0]['source_id']
        for replacement in ({'message_id':99},{'sender':{'user_id':67890}},{'group_id':55555},{'message':[]}):
            self.event.bot.get_msg.return_value={**self.native,**replacement}
            with self.assertRaises(ValueError):await history.original_url(self.settings,self.event,self.db,handle,lambda:True)
        self.settings['generation']=2
        with self.assertRaises(ValueError):history.image_reference(self.settings,self.event,self.db,handle)
        self.settings['generation']=1;self.work.scope='private:67890'
        with self.assertRaises(ValueError):self.selected()

    async def test_unready_journal_and_tampered_page_cannot_be_used_as_history(self):
        page=self.selected();changed=copy.deepcopy(page);changed['refs'][0]='0'*64
        with self.assertRaises(ValueError):await self.data(changed)
        self.db.execute("UPDATE fishbot_readonly_journals SET state='blocked'");self.db.commit()
        with self.assertRaisesRegex(ValueError,'not_ready'):self.selected()
        self.event.bot.get_msg.assert_not_awaited()

    async def test_retirement_removes_read_and_publish_authority_even_with_a_saved_reference(self):
        page=self.selected();handle=(await self.data(page))['entries'][0]['images'][0]['source_id']
        image=history.image_reference(self.settings,self.event,self.db,handle)
        # Simulate the owner's completed legal retirement projection, never a
        # history reader write. Both old references must fail closed thereafter.
        from unittest.mock import patch
        with patch('journal_storage.read_snapshot',return_value={'wire':self.messages[-1:]}):
            for ref in (page,image):
                with self.assertRaises(ValueError):await history.materialize(self.settings,self.event,self.db,ref,lambda:True)
            with self.assertRaises(ValueError):await history.original_url(self.settings,self.event,self.db,handle,lambda:True)
        self.event.bot.get_msg.assert_not_awaited()

    async def test_native_read_late_result_cannot_authorize_stale_work(self):
        handle=(await self.data())['entries'][0]['images'][0]['source_id'];entered=asyncio.Event();release=asyncio.Event();live=[True]
        async def read(**kw):entered.set();await release.wait();return self.native
        self.event.bot.get_msg=read
        task=asyncio.create_task(history.original_url(self.settings,self.event,self.db,handle,lambda:live[0]))
        await entered.wait();live[0]=False;release.set()
        with self.assertRaises(asyncio.CancelledError):await task

    async def test_group_and_scoped_journal_use_same_reader_and_preserve_unrelated_scopes(self):
        for journal_scope in ('group:55555','bot_shared'):
            db=sqlite3.connect(':memory:')
            try:
                self.work.scope='group:55555';self.extra['_hosted_journal_scope']=journal_scope
                self.event.get_group_id=lambda:'55555'
                message=event_message({'scope':self.work.scope,'sender_id':'12345','event_id':'100'},'群聊原文')
                seed(db,[message],journal_scope)
                page=history.search(self.settings,self.event,db)
                self.assertEqual(page['scope'],'group:55555');self.assertEqual(len(page['refs']),1)
                self.assertIn('群聊原文',(await history.materialize(self.settings,self.event,db,page,lambda:True))['text'])
            finally:db.close()


if __name__=='__main__':unittest.main()

"""Real reflection fixture shared by the migrated style lifecycle checks."""
import asyncio,contextvars,json,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from behavior import BehaviorStore
from context_protocol import event_message
from summary_material import snapshots


class SummaryLearningFixture:
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.path=Path(self.temporary.name)/'behavior.sqlite'
        self.store=BehaviorStore(self.path);self.db=self.store.db;self.ref=self.store.reflection
        self.scope='group:12345';self.cfg={'memory':True,'memorySummary':True,'styleLearning':True}
        self.state={'current':True};self.metrics={};self.calls=[]
        self.event=contextvars.ContextVar('summary-style-event',default='original')

    def tearDown(self):
        self.store.close();self.temporary.cleanup()

    def record(self,start=0,count=35,origins=None):
        for i in range(start,start+count):
            origin=origins[i%len(origins)] if origins else self.scope
            person=('12345678','23456789')[i%2]
            identifier=str(i);text='这波有节目效果'
            self.store.social.observe(origin,person,text,identifier)
            message=event_message({'scope':origin,'sender_id':person,'event_id':identifier},text)
            key=json.dumps([origin,identifier],separators=(',',':')) if self.scope=='bot_shared' else identifier
            self.ref.record(self.scope,person,key,text,'确实很有趣',materials=snapshots([message],self.scope))

    def result(self,kw):
        prompt=json.loads(kw['prompt']);products=[]
        for batch in prompt.get('style_learning',[]):
            products.append({'scope':batch['scope'],'patterns':[{'expression':'有节目效果','situation':'轻松开玩笑',
                'source_ids':[sample['id'] for sample in batch['samples']]}]})
        return NS(completion_text=json.dumps({'summary':'本批大家交流了轻松有趣的话题。','learning':products},ensure_ascii=False))

    async def model(self,**kw):
        self.calls.append(kw);return self.result(kw)

    def start(self,model=None):
        return self.ref.maybe_start(self.scope,self.cfg,NS(text_chat=model or self.model),lambda:self.state['current'],self.metrics,self.event)

    async def finish(self):
        if self.ref.task:await self.ref.task
        await asyncio.sleep(0)

    def patterns(self):
        return self.db.execute('SELECT scope,expression FROM social_patterns ORDER BY scope,expression').fetchall()

    def sample_count(self):
        return self.db.execute('SELECT count(*) FROM social_samples').fetchone()[0]

    def social_attempts(self):
        return self.db.execute('SELECT count(*) FROM social_learning').fetchone()[0]

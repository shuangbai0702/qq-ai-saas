import asyncio,json,os,sqlite3,sys,tempfile,unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters'/'astrbot'))
import social


class MemeSemantic(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.db=sqlite3.connect(':memory:')
        self.store=social.SocialStore(self.db,self.tmp.name)
        self.cfg={'memeEnabled':True,'memeSemantic':True,'embeddingModel':'fixture','dailyEmbeddingLimit':100}
        self.calls=[]
        self.items=[{'id':'lexical','meaning':'饮品','category':'neutral'},
                    {'id':'semantic','meaning':'喝红茶','category':'neutral'},
                    {'id':'sad','meaning':'安慰','category':'sad'},
                    {'id':'happy','meaning':'庆祝','category':'happy'}]
        self.pack=patch.object(social,'pack_catalog',side_effect=lambda root,packs:list(self.items) if 'blue-fat-fish' in (packs or []) else [])
        self.pack.start();self.cfg['memePacks']=['blue-fat-fish']
        async def create(**kw):
            self.calls.append(kw)
            return NS(data=[NS(index=i,embedding=[1.,0.] if t in ('饮品','喝红茶') else [0.,1.]) for i,t in enumerate(kw['input'])])
        self.client=NS(base_url='https://fixture.example/v1',embeddings=NS(create=create))
        self.client.with_options=lambda **kw:self.client
        self.provider=NS(client=self.client)

    def tearDown(self):
        self.pack.stop();self.store.close();self.db.close();self.tmp.cleanup()

    async def context(self,text='饮品',scope='group:1',current=lambda:True):
        return await self.store.context_semantic(scope,text,self.cfg,self.provider,current)

    def ids(self,result):return [i['id'] for i in result.get('meme_candidates',[])]

    async def test_ranking_cache_diversity_and_emotion(self):
        # Make lexical overlap disagree with semantic similarity.
        self.items[0]['meaning']='饮品饮品'
        result=await self.context()
        self.assertEqual(self.ids(result)[0],'semantic')
        self.assertNotIn('path',result['meme_candidates'][0])
        self.assertEqual(len(self.calls[0]['input']),5)
        await self.context();self.assertEqual(self.calls[-1]['input'],['饮品'])
        result=await self.context('难过')
        self.assertLess(self.ids(result).index('sad'),self.ids(result).index('happy'))
        self.items.extend({'id':f'extra{i}','meaning':'喝红茶','category':'neutral'} for i in range(10))
        result=await self.context()
        self.assertLessEqual(Counter(i.get('category') for i in result['meme_candidates'])['neutral'],2)
        for key in ('meme_rule','participation_rule'):
            self.assertEqual(result[key],self.store.context('group:1','饮品',self.cfg)[key])

    async def test_model_endpoint_and_content_isolation(self):
        await self.context()
        self.cfg['embeddingModel']='other';await self.context();self.assertEqual(len(self.calls[-1]['input']),5)
        self.client.base_url='https://other.example/v1';await self.context();self.assertEqual(len(self.calls[-1]['input']),5)
        self.items[0]['meaning']='新描述';await self.context();self.assertEqual(len(self.calls[-1]['input']),2)
        self.assertIn('新描述',self.calls[-1]['input'])
        await self.context(scope='group:2');self.assertEqual(len(self.calls[-1]['input']),5)

    async def test_real_scope_review_disabled_catalog_and_favorites(self):
        self.items.clear()
        for identifier,scope,status in [('cap_local','group:1','approved'),('cap_other','group:2','approved'),('cap_pending','group:1','pending')]:
            self.db.execute('INSERT INTO sticker_inbox VALUES(?,?,?,?,?,?,?,1,?)',(identifier,scope,'s','e',status,identifier,1,b'png'))
        self.db.commit()
        reactions=Path(self.tmp.name)/'reactions';reactions.mkdir()
        (reactions/'disabled.png').write_bytes(b'png')
        (reactions/'disabled.json').write_text(json.dumps({'id':'disabled','meaning':'disabled','enabled':False}))
        result=await self.context()
        self.assertEqual(self.ids(result),['cap_local'])
        self.assertEqual(self.calls[-1]['input'],['饮品','cap_local'])
        self.db.execute("UPDATE sticker_inbox SET status='rejected' WHERE id='cap_local'");self.db.commit()
        count=len(self.calls);self.assertEqual(self.ids(await self.context()),[]);self.assertEqual(len(self.calls),count)
        folder=Path(self.tmp.name)/'qq-favorites';(folder/'images').mkdir(parents=True)
        identifier='qqf_'+'a'*16;(folder/'images'/(identifier+'.png')).write_bytes(b'png')
        index={'account':'12345','enabled':True,'items':[{'id':identifier,'file':identifier+'.png','enabled':True,'meaning':'收藏茶','manual':True}]}
        (folder/'index.json').write_text(json.dumps(index),encoding='utf8');self.cfg['_qqAccount']='12345'
        self.assertEqual(self.ids(await self.context('发收藏表情')),[identifier])
        self.assertEqual(self.calls[-1]['input'],['发收藏表情','收藏茶'])
        self.cfg['_qqAccount']='67890';count=len(self.calls)
        self.assertEqual(self.ids(await self.context('发收藏表情')),[]);self.assertEqual(len(self.calls),count)

    async def test_opt_in_budget_and_failures_fall_back(self):
        expected=self.store.context('group:1','饮品',self.cfg)
        for key,value in [('memeSemantic',False),('embeddingModel',''),('dailyEmbeddingLimit',0)]:
            old=self.cfg[key];self.cfg[key]=value
            self.assertEqual(await self.context(),expected);self.assertFalse(self.calls)
            self.cfg[key]=old
        self.cfg['memeEnabled']=False;self.assertNotIn('meme_candidates',await self.context());self.assertFalse(self.calls)
        self.cfg['memeEnabled']=True
        for mode in ('exception','nan','count','zero','dimension'):
            async def broken(**kw):
                if mode=='exception':raise ValueError('unsupported')
                vectors=[[1.,0.] for _ in kw['input']]
                if mode=='nan':vectors[0]=[float('nan'),0.]
                if mode=='count':vectors.pop()
                if mode=='zero':vectors[0]=[0.,0.]
                if mode=='dimension':vectors[0]=[1.]
                return NS(data=[NS(index=i,embedding=v) for i,v in enumerate(vectors)])
            self.client.embeddings.create=broken
            self.assertEqual(await self.context(),expected)
            self.assertEqual(len(self.store.meme_vectors),0)
        self.provider=NS();self.assertEqual(await self.context(),expected)

    async def test_late_close_replace_disable_catalog_and_cancel(self):
        for mode in ('replace','disable','catalog','endpoint','close','cancel'):
            entered=asyncio.Event();release=asyncio.Event();valid=[True]
            async def delayed(**kw):
                entered.set();await release.wait()
                return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
            self.client.embeddings.create=delayed
            task=asyncio.create_task(self.context(current=lambda:valid[0]));await entered.wait()
            if mode=='replace':valid[0]=False
            elif mode=='disable':self.cfg['memeSemantic']=False
            elif mode=='catalog':self.items[0]['meaning']='changed'
            elif mode=='endpoint':self.client.base_url='https://changed.example/v1'
            elif mode=='close':self.store.close();self.store.close()
            else:task.cancel()
            release.set()
            if mode=='cancel':
                with self.assertRaises(asyncio.CancelledError):await task
            else:
                result=await task
                if mode in ('replace','close'):self.assertEqual(result,{})
            self.assertEqual(len(self.store.meme_vectors),0)
            self.assertFalse(self.store.meme_embedding_lock.locked())
            self.cfg['memeSemantic']=True;self.store.closed=False

    async def test_batches_bounded_index_and_second_batch_failure(self):
        self.items[:]=[{'id':f'item{i}','meaning':f'meaning{i}','category':f'cat{i}'} for i in range(300)]
        await self.context()
        self.assertTrue(all(len(c['input'])<=16 for c in self.calls))
        self.assertEqual(sum(len(c['input']) for c in self.calls),social.MEME_INDEX_LIMIT+1)
        self.assertEqual(len(self.store.meme_vectors),social.MEME_INDEX_LIMIT)
        self.cfg['embeddingModel']='second';await self.context()
        self.assertEqual(len(self.store.meme_vectors),social.MEME_INDEX_LIMIT)
        self.store.meme_vectors.clear();count=[0]
        async def fail_second(**kw):
            count[0]+=1
            if count[0]==2:raise ValueError('batch failed')
            return NS(data=[NS(index=i,embedding=[1.,0.]) for i in range(len(kw['input']))])
        self.client.embeddings.create=fail_second
        self.assertEqual(await self.context(),self.store.context('group:1','饮品',self.cfg))
        self.assertEqual(len(self.store.meme_vectors),0)


if __name__=='__main__':unittest.main()

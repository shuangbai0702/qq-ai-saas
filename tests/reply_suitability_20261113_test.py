"""One physical prefix, scene-specific observations and unchanged decisions."""
import asyncio,json,sys,time,unittest
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'adapters/astrbot'),str(Path(__file__).resolve().parents[1]/'references/astrbot')]
from group_freshness import GroupFreshness,review_request


class Suitability(unittest.IsolatedAsyncioTestCase):
    def request(self,group,payload):return review_request(payload,'固定人格','11111','99999',group=group)
    def test_private_and_group_share_exact_system_and_reference_blocks(self):
        base={'memory':{'memory_foundation':{'summary':'固定认知'},'cross_scope_guidance':'固定来源合同'},'draft':'回复'}
        a=self.request(False,{**base,'scope':'private:11111'})
        b=self.request(True,{**base,'scope':'group:55555'})
        self.assertEqual(a['system_prompt'],b['system_prompt'])
        self.assertEqual(a['contexts'],b['contexts'])
        self.assertEqual(json.loads(a['prompt'])['context']['scene'],'private')
        self.assertEqual(json.loads(b['prompt'])['context']['scene'],'group')
        self.assertIn('私聊：只按对方实际说的话',a['system_prompt'])
        self.assertIn('目标人与全群两个维度',b['system_prompt'])
        self.assertIn('旧队列',b['system_prompt'])
    async def test_scene_observations_and_model_decisions_are_not_replaced_by_queue_heuristics(self):
        cases=[
            (False,'对方纠正时间','replace','改成明天下午。'),
            (True,'目标人补充意图','replace','按你的补充来回答。'),
            (True,'旁人无关插话','keep',''),
            (True,'全群转入严肃紧急话题','drop',''),
            (True,'多人分别交流但原回复仍自然','keep',''),
        ]
        for group,text,action,replacement in cases:
            with self.subTest(text=text):
                f=GroupFreshness();scope='group:55555' if group else 'private:11111';start=time.monotonic()
                f.observe(scope,'root','11111','原问题');cursor=f.cursor(scope)
                f.observe(scope,'new','11111' if not group or action=='replace' else '22222',text)
                seen=[]
                async def call(payload):
                    r=self.request(group,{**payload,'original_message':{'event':'root','sender_id':'11111','text':'原问题'},
                        'conversation':{'participants':[{'id':'11111'},{'id':'22222'}], 'timeline':[{'sender_id':'11111','text':'原问题'}]},
                        'memory':{'relations':[{'subject':'11111','impression':'熟悉的交流对象'}]}})
                    seen.append(r)
                    return json.dumps({'action':action,'text':replacement,'reason':'synthetic_judgment'})
                result=await f.check(scope,cursor,start,'root','当前草稿','',call,lambda:True)
                self.assertEqual(result['action'],action);self.assertEqual(len(seen),1)
                data=json.loads(seen[0]['prompt'])
                self.assertEqual(data['review']['latest_messages'][0]['text'],text)
                self.assertEqual(data['review']['original_message']['sender_id'],'11111')
                self.assertEqual(data['context']['conversation']['participants'][1]['id'],'22222')
                self.assertEqual(data['changes']['memory']['relations'][0]['impression'],'熟悉的交流对象')
                if action=='replace':self.assertEqual(result['text'],replacement)
                # Same destination version: no additional call for queue size,
                # passage of time or a second bubble alone.
                result=await f.check(scope,result['cursor'],start,'root','草稿','',call,lambda:True)
                self.assertEqual(result['action'],'keep');self.assertEqual(len(seen),1)


if __name__=='__main__':unittest.main()

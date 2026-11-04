import json,sqlite3,sys,tempfile,unittest,time,asyncio
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'adapters/astrbot'))
from PIL import Image
from social import SocialStore
from runtime_care import Ambient
from sticker_collection import StickerCollector

class Alignment(unittest.TestCase):
 def test_animated_pack_and_actual_selection(self):
  with tempfile.TemporaryDirectory() as folder:
   s=SocialStore(sqlite3.connect(':memory:'),folder)
   cfg={'memeEnabled':True,'memePacks':['blue-fat-fish'],'memeProbability':.35}
   items=s.catalog('group:12345',packs=cfg['memePacks']);pack=[i for i in items if i['id'].startswith('bf_')]
   self.assertEqual(len(pack),157);self.assertTrue(all(Path(i['path']).stat().st_size<=1048576 for i in pack))
   with Image.open(pack[0]['path']) as gif:
    self.assertGreater(gif.n_frames,1)
    self.assertEqual(gif.convert('RGBA').getchannel('A').getextrema(),(0,255),'transparent background must survive normalization')
   text='[MEME:'+pack[0]['id']+']'
   self.assertEqual(s.select_image(text,'group:12345',{**cfg,'memeProbability':0,'dailyMemeLimit':0})[1]['id'],pack[0]['id'])
   choices=s.context('group:12345','今天不开心，真的难过',cfg)['meme_candidates']
   self.assertEqual(choices[0]['category'],'sad');self.assertLessEqual(sum(i.get('category')=='sad' for i in choices),2)
   (Path(folder)/'meme-pack-settings.json').write_text(json.dumps({'disabled':[pack[0]['id']]}))
   self.assertIsNone(s.select_image(text,'group:12345',cfg)[1]);remaining=s.catalog('group:12345');self.assertEqual(len(remaining),156);self.assertTrue(all(i['id'].startswith('bf_') for i in remaining))
   s.close()
 def test_candidates_available_for_model_choice_without_probability(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=sqlite3.connect(':memory:');s=SocialStore(db,tmp);cfg={'memeEnabled':True,'memeProbability':0,'dailyMemeLimit':0}
   self.assertTrue(s.image_available('group:12345',cfg));self.assertTrue(s.context('group:12345','你好',cfg)['meme_candidates'])
   self.assertIsNone(s.select_image('你好','group:12345',cfg)[1]);self.assertFalse(s.image_available('group:12345',{**cfg,'memeEnabled':False}));s.close();db.close()

 def test_ambient_config_bound(self):
  a=Ambient()
  for i in range(70):a.observe('group:1','sender',str(i),100+i,50)
  self.assertEqual(len(a.context('group:1',170)),50);self.assertEqual(a.context('group:2',170),[])
 def test_collection_all_groups_keeps_private_out(self):
  async def run():
   db=sqlite3.connect(':memory:');collector=StickerCollector(db)
   raw={'message_id':'1','message':[{'type':'image','data':{'sub_type':1,'url':'https://gchat.qpic.cn/fixture'}}]}
   import sticker_collection
   async def download(url):return (root/'templates/reactions/laugh.png').read_bytes()
   try:
    cfg={'stickerCollect':True,'stickerAllGroups':True,'stickerGroups':[]}
    collector.observe(raw,'private:12345','sender','bot',cfg,lambda:True,{},fetch=download)
    self.assertIsNone(collector.task)
    collector.observe(raw,'group:12345','sender','bot',cfg,lambda:True,{},fetch=download)
    self.assertIsNotNone(collector.task);await collector.task
    self.assertEqual(db.execute('SELECT count(*) FROM sticker_inbox').fetchone()[0],1)
   finally:collector.close();db.close()
  asyncio.run(run())
if __name__=='__main__':unittest.main(verbosity=2)

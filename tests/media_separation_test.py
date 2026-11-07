import asyncio,io,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from sticker_collection import StickerCollector,manage,png_thumbnail,download
from behavior import BehaviorStore

PNG=Path('templates/reactions/laugh.png').read_bytes()
CFG={'stickerCollect':True,'stickerGroups':['12345'],'dailyStickerCollectLimit':10}
RAW={'message_id':123,'message':[{'type':'image','data':{'sub_type':1,'url':'https://gchat.qpic.cn/test'}}]}

class MediaTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite');self.c=self.store.social.stickers;self.metrics={}
 async def asyncTearDown(self):
  task=self.c.task;self.store.close()
  if task:
   try:await task
   except asyncio.CancelledError:pass
  self.tmp.cleanup()
 async def collect(self,**kwargs):
  async def fetch(url):return PNG
  self.c.observe(kwargs.get('raw',RAW),kwargs.get('scope','group:12345'),'123456','90000',kwargs.get('cfg',CFG),kwargs.get('current',lambda:True),self.metrics,fetch=kwargs.get('fetch',fetch))
  if self.c.task:await self.c.task
  return manage(self.store.db,{'action':'list_stickers'})['items']
 async def test_marked_image_enters_inbox_not_reply_candidates(self):
  items=await self.collect();self.assertEqual(len(items),1);self.assertEqual(items[0]['status'],'pending');self.assertFalse(self.c.catalog('group:12345'))
 async def test_approval_scoped_and_removal_blocks_same_content(self):
  item=(await self.collect())[0];body={'action':'review_sticker','id':item['id'],'expectedVersion':1,'status':'approved','meaning':'轻松开玩笑'}
  self.assertTrue(manage(self.store.db,body)['ok']);self.assertEqual(manage(self.store.db,body)['status'],409)
  self.assertEqual(len(self.c.catalog('group:12345')),1);self.assertFalse(self.c.catalog('group:54321'))
  manage(self.store.db,{**body,'expectedVersion':2,'status':'rejected'})
  self.store.db.execute('DELETE FROM sticker_attempts');self.store.db.commit();self.assertFalse(await self.collect());self.assertFalse(self.c.catalog('group:12345'))
 async def test_ordinary_photo_and_unauthorized_group_not_collected(self):
  raw=json.loads(json.dumps(RAW));raw['message'][0]['data']['sub_type']=0
  self.assertFalse(await self.collect(raw=raw));self.assertFalse(await self.collect(scope='group:54321'))
 async def test_disabled_and_zero_budget_do_not_download(self):
  self.assertFalse(await self.collect(cfg={**CFG,'stickerCollect':False}));self.assertFalse(await self.collect(cfg={**CFG,'dailyStickerCollectLimit':0}));self.assertEqual(self.metrics,{})
 async def test_failed_download_consumes_attempt_and_is_bounded(self):
  calls=[]
  async def fail(url):calls.append(1);raise OSError('no download')
  self.assertFalse(await self.collect(fetch=fail));self.assertFalse(await self.collect(fetch=fail));self.assertEqual(len(calls),1);self.assertEqual(self.metrics['stickerCollectFailures'],1)
 async def test_config_replacement_and_stop_reject_late_result(self):
  entered,release=asyncio.Event(),asyncio.Event();current=True
  async def held(url):entered.set();await release.wait();return PNG
  self.c.observe(RAW,'group:12345','123456','90000',CFG,lambda:current,self.metrics,fetch=held)
  await entered.wait();current=False;release.set();await self.c.task
  self.assertFalse(manage(self.store.db,{'action':'list_stickers'})['items'])
 async def test_stopped_collector_cancels_pending_download(self):
  entered=asyncio.Event()
  async def held(url):entered.set();await asyncio.Event().wait()
  self.c.observe(RAW,'group:12345','123456','90000',CFG,lambda:True,self.metrics,fetch=held);await entered.wait();self.c.close()
  with self.assertRaises(asyncio.CancelledError):await self.c.task
 async def test_reject_untrusted_url_before_network(self):
  # Only URLs the origin gate rejects pre-network belong here. The http QQ
  # CDN form is deliberately rewritten to https and fetched (OneBot support),
  # so its outcome depends on the real network and is not a unit assertion.
  for url in ['https://127.0.0.1/a','https://gchat.qpic.cn.evil.test/a','file:///tmp/a','https://user:pass@gchat.qpic.cn/a']:
   with self.assertRaises(ValueError):await download(url)
 async def test_pending_cap_and_bot_isolation(self):
  for i in range(10):self.store.db.execute("INSERT INTO sticker_inbox VALUES(?, 'group:12345','1','e','pending','',0,1,?)",('cap_'+str(i),PNG))
  self.store.db.commit();self.assertEqual(len(await self.collect()),10);self.assertIsNone(self.c.task)
  other=BehaviorStore(Path(self.tmp.name)/'other'/'behavior.sqlite')
  try:self.assertFalse(manage(other.db,{'action':'list_stickers'})['items'])
  finally:other.close()
 async def test_gallery_is_separate_and_only_explicit_requests(self):
  root=Path(self.tmp.name)/'gallery';root.mkdir();(root/'gal_test.png').write_bytes(PNG);(root/'gal_test.json').write_text(json.dumps({'id':'gal_test','meaning':'角色日常照片'}))
  social=self.store.social
  self.assertFalse(any(x['id']=='gal_test' for x in social.catalog('group:12345')))
  self.assertIsNone(social.select_gallery('[IMAGE:gal_test]',{'galleryEnabled':True},'你好'))
  self.assertIsNone(social.select_gallery('[IMAGE:gal_test]',{},'发张图片'))
  self.assertEqual(social.select_gallery('[IMAGE:gal_test]',{'galleryEnabled':True},'发张图片')['id'],'gal_test')
 async def test_first_frame_and_invalid_images(self):
  from PIL import Image
  gif=io.BytesIO();Image.new('RGB',(8,8),'red').save(gif,format='GIF',save_all=True,append_images=[Image.new('RGB',(8,8),'blue')])
  self.assertTrue(png_thumbnail(gif.getvalue()).startswith(b'\x89PNG'))
  for raw in [b'bad',b'x'*1048577]:
   with self.assertRaises(Exception):png_thumbnail(raw)

if __name__=='__main__':
 if '--seed' in sys.argv:
  target=Path(sys.argv[-1]);target.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(target);c=StickerCollector(db);db.execute("INSERT INTO sticker_inbox VALUES('cap_0123456789abcdef','group:12345','123456','123','pending','',100,1,?)",(PNG,));db.commit();db.close()
 else:unittest.main(verbosity=2)

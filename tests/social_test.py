import asyncio,json,sqlite3,sys,tempfile,unittest,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from social import SocialStore
from style_learning_summary_fixture import SummaryLearningFixture
FISH_ID=json.loads(Path('templates/meme-packs/blue-fat-fish/index.json').read_text(encoding='utf8'))['items'][0]['id']

class SocialTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.db=sqlite3.connect(self.root/'test.db');self.s=SocialStore(self.db,self.root)
 def tearDown(self):self.s.close();self.db.close();self.temp.cleanup()
 def batch(self,scope='group:1'):
  for i in range(12):self.s.observe(scope,'u'+str(i%2),'这波真有节目效果啊',str(i),now=1000+i)
  return self.s.learning_batch(scope,now=1100)
 def learned(self,scope='group:1',now=None):
  batch=self.batch(scope);raw=json.dumps({'patterns':[{'expression':'有节目效果','situation':'轻松开玩笑','source_ids':[batch[0]['id'],batch[1]['id']]}]});return self.s.accept_learning(scope,batch,raw,now=time.time() if now is None else now)
 def test_cold_expression_can_return_for_related_context(self):self.learned(now=1200);self.assertEqual(self.s.expressions('group:1','开玩笑')[0]['expression'],'有节目效果')
 def test_expired_samples_never_enter_new_learning(self):self.batch();self.assertEqual(self.s.learning_batch('group:1',now=1000000),[])
 def test_pruned_samples_do_not_reuse_learning_ids(self):
  batch=self.batch();self.s.reserve_learning('group:1',batch,1100);self.db.execute('DELETE FROM social_samples');self.db.commit()
  for i in range(12):self.s.observe('group:1',str(i%2),'新的一波有节目效果',str(i+50),now=3000+i)
  fresh=self.s.learning_batch('group:1',now=3100);self.assertEqual(len(fresh),12);self.assertGreater(min(x['id'] for x in fresh),max(x['id'] for x in batch))
 def test_group_batch_threshold(self):self.assertEqual(len(self.batch()),12)
 def test_legacy_migration_preserves_samples_and_watermark(self):
  db=sqlite3.connect(':memory:');db.executescript("CREATE TABLE social_samples(id INTEGER PRIMARY KEY,scope TEXT,event TEXT,subject TEXT,text TEXT,created REAL,UNIQUE(scope,event));INSERT INTO social_samples VALUES(5,'group:1','old','u1','有效的旧表达',1000);CREATE TABLE social_learning(scope TEXT,started REAL,watermark INTEGER);INSERT INTO social_learning VALUES('group:1',1000,99);")
  migrated=SocialStore(db,self.root);migrated.observe('group:1','u2','新的一条表达','new',3000)
  self.assertEqual(db.execute('SELECT id FROM social_samples WHERE event="old"').fetchone()[0],5)
  self.assertGreater(db.execute('SELECT id FROM social_samples WHERE event="new"').fetchone()[0],99)
  migrated.close();db.close()
 def test_migration_failure_rolls_back_table_rename(self):
  class FailingConnection(sqlite3.Connection):
   fail=False
   def execute(self,sql,*args):
    if self.fail and sql.startswith('INSERT INTO social_samples SELECT'):raise sqlite3.OperationalError('injected migration failure')
    return super().execute(sql,*args)
  db=sqlite3.connect(':memory:',factory=FailingConnection);db.executescript("CREATE TABLE social_samples(id INTEGER PRIMARY KEY,scope TEXT,event TEXT,subject TEXT,text TEXT,created REAL,UNIQUE(scope,event));INSERT INTO social_samples VALUES(5,'group:1','old','u1','保留旧数据',1000);")
  db.fail=True
  with self.assertRaises(sqlite3.OperationalError):SocialStore(db,self.root)
  self.assertEqual(db.execute('SELECT count(*) FROM social_samples').fetchone()[0],1)
  self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='social_samples_legacy'").fetchone())
  db.fail=False;migrated=SocialStore(db,self.root);migrated.close();db.close()
 def test_private_never_learned(self):self.assertEqual(self.batch('private:1'),[])
 def test_duplicate_events_ignored(self):self.batch();self.batch();self.assertEqual(self.db.execute('SELECT count(*) FROM social_samples').fetchone()[0],12)
 def test_repeated_expression_with_evidence(self):self.assertEqual(self.learned(),1);self.assertEqual(self.s.expressions('group:1','开玩笑')[0]['expression'],'有节目效果')
 def test_no_cross_group(self):self.learned();self.assertEqual(self.s.expressions('group:2','开玩笑'),[])
 def test_invented_evidence_denied(self):batch=self.batch();self.assertEqual(self.s.accept_learning('group:1',batch,json.dumps({'patterns':[{'expression':'哈哈','situation':'轻松聊天','source_ids':[999,998]}]})),0)
 def test_one_sender_cannot_teach_shared_expression(self):
  for i in range(12):self.s.observe('group:1','same','有节目效果',str(i),1000+i)
  self.assertEqual(self.s.learning_batch('group:1',1100),[])
 def test_learning_retry_and_success_watermark(self):
  batch=self.batch();attempt=self.s.reserve_learning('group:1',batch,1100);self.assertEqual(self.s.learning_batch('group:1',1200),[])
  self.assertEqual(len(self.s.learning_batch('group:1',3000)),12,'an uncompleted attempt must retain its samples')
  self.s.accept_learning('group:1',batch,'{"patterns":[]}',now=3000,attempt=attempt)
  self.assertEqual(self.s.learning_batch('group:1',3000),[],'a valid empty result still consumes the batch')
 def test_learning_has_no_global_daily_quota(self):
  batch=self.batch()
  for i in range(5):self.s.reserve_learning('group:other',batch,1100+i)
  self.assertEqual(len(self.s.learning_batch('group:1',3000)),12)
 def test_bad_learning_response_does_not_consume_samples(self):
  batch=self.batch();attempt=self.s.reserve_learning('group:1',batch,1100)
  self.s.accept_learning('group:1',batch,'invalid',now=1101,attempt=attempt)
  self.assertEqual(len(self.s.learning_batch('group:1',3000)),12)
 def test_learning_survives_restart(self):self.learned();second=SocialStore(self.db,self.root);self.assertEqual(len(second.expressions('group:1','开玩笑')),1);second.close()
 def test_no_config_no_context(self):self.learned();self.assertEqual(self.s.context('group:1','你好',{}),{})
 def test_no_filesystem_paths_in_prompt(self):self.assertNotIn('path',json.dumps(self.s.context('group:1','你好',{'memeEnabled':True})))
 def test_group_platform_image_available_without_native_qq_faces(self):
  cfg={'memeEnabled':True,'memeProbability':.7,'qqFaces':False,'memePacks':['blue-fat-fish']}
  ctx=self.s.context('group:12345','发个表情包',cfg)
  self.assertTrue(ctx['meme_candidates'])
  image_id=ctx['meme_candidates'][0]['id']
  _,image=self.s.select_image('[MEME:'+image_id+']','group:12345',cfg,1000)
  self.assertIsNotNone(image);self.assertTrue(Path(image['path']).is_file())
 def test_gallery_stays_independent_when_memes_disabled(self):
  from unittest.mock import patch
  with patch.object(self.s,'catalog',return_value=[{'id':'photo','meaning':'自然风景','path':'private-path'}]):
   context=self.s.context('group:1','给我看看图片',{'galleryEnabled':True,'memeEnabled':False})
  self.assertEqual(context['gallery_candidates'][0]['id'],'photo')
  self.assertNotIn('meme_candidates',context)
 def test_image_ranking_prefers_phrase_over_incidental_character(self):
  ctx=self.s.context('group:1','喝茶',{'memeEnabled':True},_meme_catalog=[{'id':'irrelevant','meaning':'喝彩','category':'other'},{'id':'relevant','meaning':'喝茶；用于休息喝茶','category':'other'}])
  self.assertEqual(ctx['meme_candidates'][0]['id'],'relevant')
  mixed=[{'id':'fish','meaning':'打招呼','source':'platform_pack'},{'id':'qqf_fixture','meaning':'挥手问好','source':'qq_favorite_cache'}]
  ctx=self.s.context('group:1','我收藏了一个视频',{'memeEnabled':True},_meme_catalog=mixed)
  self.assertEqual({i['id'] for i in ctx['meme_candidates']},{'fish','qqf_fixture'})
  self.assertNotIn('meme_rule',ctx,'the static platform policy owns this rule, not mutable candidate data')
  from meme_reactions import SELECTION_RULE
  self.assertIn('不为来源偏好牺牲相关性',SELECTION_RULE)
  many=[{'id':'p'+str(i),'meaning':'笑死哈哈开心','source':'platform_pack'} for i in range(12)]+[mixed[1]]
  ctx=self.s.context('group:1','哈哈开心',{'memeEnabled':True},_meme_catalog=many)
  self.assertEqual(len(ctx['meme_candidates']),8)
  self.assertIn('qq_favorite_cache',{i['source'] for i in ctx['meme_candidates']})
 def test_model_text_only_does_not_get_forced_image(self):
  cfg={'memeEnabled':True,'memeProbability':0,'dailyMemeLimit':0,'memePacks':['blue-fat-fish']}
  context=self.s.context('group:1','想你了',cfg)
  self.assertTrue(context['meme_candidates'])
  self.assertIsNone(self.s.select_image('哼，现在才想我呀。','group:1',cfg,1000,candidates=context['meme_candidates'])[1])
  chosen=context['meme_candidates'][0]['id']
  self.assertEqual(self.s.select_image('[MEME:'+chosen+']','group:1',cfg,1000,candidates=context['meme_candidates'])[1]['id'],chosen)
 def test_model_selection_requires_current_owned_candidate(self):
  cfg={'memeEnabled':True,'memePacks':['blue-fat-fish']};context=self.s.context('group:1','想你了',cfg)
  chosen=context['meme_candidates'][0]['id'];choose=lambda text,request,offered:self.s.select_image(text,'group:1',cfg,1000,candidates=offered,request_text=request)[1]
  self.assertIsNotNone(choose('[MEME:'+chosen+']','想你了',context['meme_candidates']))
  self.assertIsNone(choose('[MEME:'+chosen+']','家人重病了',context['meme_candidates']))
  self.assertIsNone(choose('[MEME:invalid]','想你了',context['meme_candidates']))
  self.assertIsNone(choose('[MEME:'+chosen+']','想你了',[{'id':'qqf_fake'}]))
  self.assertIsNone(choose('[MEME:qqf_fake]','想你了',[{'id':'qqf_fake'}]))
  self.assertEqual(self.s.context('group:1','普通聊天',{**cfg,'memePacks':[]})['meme_candidates'],[])
 def test_candidate_eligibility_has_no_daily_or_probability_gate(self):
  cfg={'memeEnabled':True,'memePacks':['blue-fat-fish'],'memeProbability':0,'dailyMemeLimit':0}
  self.assertTrue(self.s.image_available('group:1',cfg,1000,'普通聊天'))
  self.assertEqual(self.s.image_status('group:1',cfg,1000,'家人重病了')['reason'],'sensitive')
  self.assertEqual(self.s.image_status('group:1',{**cfg,'memeEnabled':False},1000)['reason'],'disabled')
  self.s.reserve_image('group:1',1000)
  self.assertEqual(self.s.image_status('group:1',cfg,1001),{'reason':'cooldown','retryAfterSeconds':19})
  self.assertTrue(self.s.image_available('group:2',cfg,1001))
  self.assertTrue(self.s.image_available('group:1',cfg,1020))
 def test_no_candidates_cannot_send_invented_marker(self):
  cfg={'memeEnabled':True,'memePacks':['blue-fat-fish']}
  self.assertIsNone(self.s.select_image('配置保存了。[MEME:laugh]','group:1',cfg,1000,candidates=[])[1])
 def test_unoffered_current_pack_item_is_not_silently_substituted(self):
  cfg={'memeEnabled':True,'memePacks':['blue-fat-fish']}
  item=self.s.catalog('group:1',packs=cfg['memePacks'])[0]
  self.assertIsNone(self.s.select_image('[MEME:'+item['id']+']','group:1',cfg,1000,candidates=[{'id':'unrelated_menu_item'}])[1])
 def test_serious_model_response_protects_even_ordinary_request(self):
  self.assertIsNone(self.s.select_image('听说他家人重病了。[MEME:laugh]','group:1',{'memeEnabled':True},1000,request_text='刚刚在聊什么')[1])
 def test_confused_context_offers_relevant_reaction(self):
  ctx=self.s.context('group:1','我有点困惑，没想明白',{'memeEnabled':True,'memePacks':['blue-fat-fish']})
  self.assertTrue(any(x.get('category')=='confused' for x in ctx['meme_candidates'][:2]))
 def test_valid_image_and_internal_tag_removed(self):text,img=self.s.select_image('哈哈[MEME:'+FISH_ID+']','private:1',{'memeEnabled':True,'memeProbability':1},1000);self.assertEqual(text,'哈哈');self.assertTrue(Path(img['path']).is_file());self.assertEqual(Path(img['path']).suffix,'.gif')
 def test_invalid_id_cannot_read_path(self):text,img=self.s.select_image('你好[MEME:../../master.key]','p',{'memeEnabled':True,'memeProbability':1},1000);self.assertEqual(text,'你好');self.assertIsNone(img)
 def test_disabled_always_strips_marker(self):self.assertEqual(self.s.select_image('[MEME:laugh]','p',{},1000),('',None))
 def test_cooldown_attempt_reservation(self):self.s.reserve_image('p',1000);self.assertIsNone(self.s.select_image('[MEME:'+FISH_ID+']','p',{'memeEnabled':True},1001)[1]);self.assertIsNotNone(self.s.select_image('[MEME:'+FISH_ID+']','p',{'memeEnabled':True,'memeProbability':1},1061)[1])
 def test_old_daily_cap_and_zero_probability_do_not_block_or_erase_stats(self):
  for i in range(80):self.s.reserve_image('p',1000+i*20)
  self.assertIsNotNone(self.s.select_image('[MEME:'+FISH_ID+']','other',{'memeEnabled':True,'dailyMemeLimit':0,'memeProbability':0},3000)[1])
  self.assertIsNotNone(self.s.select_image('[MEME:'+FISH_ID+']','p',{'memeEnabled':True,'dailyMemeLimit':50,'memeProbability':0},3000)[1])
  self.assertEqual(self.db.execute('SELECT sum(count) FROM social_images').fetchone()[0],80)
 def test_retired_reactions_never_return_or_send_even_with_legacy_override(self):
  folder=self.root/'reactions';folder.mkdir()
  for identifier in ('hug','laugh','thinking'):
   (folder/(identifier+'.json')).write_text(json.dumps({'id':identifier,'meaning':'旧图仍启用','enabled':True}))
   (folder/(identifier+'.png')).write_bytes(Path('templates/reactions/'+identifier+'.png').read_bytes())
  for packs in (None,[],['blue-fat-fish']):
   cfg={'memeEnabled':True,**({'memePacks':packs} if packs is not None else {})}
   context=self.s.context('group:1','想你了',cfg)
   self.assertTrue(all(x['id'].startswith('bf_') for x in context['meme_candidates']))
   self.assertEqual(bool(context['meme_candidates']),packs!=[])
   for identifier in ('hug','laugh','thinking'):
    text,image=self.s.select_image('在呢[MEME:'+identifier+']','group:1',cfg,1000,candidates=[{'id':identifier}])
    self.assertEqual(text,'在呢');self.assertIsNone(image,'old offered or remembered IDs must not revive retired assets')
 def test_custom_upload_remains_available_without_fish_pack(self):
  identifier='usr_0123456789abcdef';folder=self.root/'reactions';folder.mkdir();(folder/(identifier+'.json')).write_text(json.dumps({'id':identifier,'meaning':'自定义场景'}));(folder/(identifier+'.png')).write_bytes(Path('templates/reactions/laugh.png').read_bytes())
  cfg={'memeEnabled':True,'memePacks':[]};context=self.s.context('group:1','自定义场景',cfg)
  self.assertEqual([x['id'] for x in context['meme_candidates']],[identifier]);self.assertIsNotNone(self.s.select_image('[MEME:'+identifier+']','group:1',cfg,1000,candidates=context['meme_candidates'])[1])

class LifecycleTests(SummaryLearningFixture,unittest.IsolatedAsyncioTestCase):
 async def test_failed_model_preserves_learning_batch_across_reopen(self):
  from behavior import BehaviorStore
  self.record()
  async def fail(**kwargs):raise ConnectionError('summary model unavailable')
  self.start(fail);await self.finish()
  self.assertEqual(self.metrics['summaryFailures'],1);self.assertEqual(self.social_attempts(),0)
  self.assertEqual(self.sample_count(),35);self.assertEqual(self.patterns(),[])
  self.store.close();self.store=BehaviorStore(self.path);self.db=self.store.db;self.ref=self.store.reflection
  self.assertEqual(self.sample_count(),35)
  self.db.execute('UPDATE memory_reflection_retry SET next_at=0');self.db.commit()
  self.start();await self.finish()
  self.assertEqual(self.patterns(),[(self.scope,'有节目效果')])
 async def test_late_learner_cannot_write_after_replacement(self):
  self.record();entered,release=asyncio.Event(),asyncio.Event()
  async def model(**kw):entered.set();await release.wait();return self.result(kw)
  self.start(model);await entered.wait();self.state['current']=False;release.set();await self.finish()
  self.assertEqual(self.patterns(),[]);self.assertEqual(self.sample_count(),35)
 async def test_close_cancels_pending_learning(self):
  self.record();entered=asyncio.Event()
  async def model(**kw):entered.set();await asyncio.Event().wait()
  self.start(model);await entered.wait();task=self.ref.task;self.ref.close()
  with self.assertRaises(asyncio.CancelledError):await task
  self.assertEqual(self.patterns(),[]);self.assertEqual(self.sample_count(),35)
if __name__=='__main__':unittest.main(verbosity=2)

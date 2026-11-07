"""D2 unified audience: source attribution, self/big gate, core_note bypass.

Offline fixtures reproduce the audited cross-scope patterns (self big id=52
entering non-owner private requests; another private chat's small id=53 being
carried across scopes by the global rolling query) and pin the fixed assembly.
"""
import contextvars,json,sys,tempfile,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from behavior import BehaviorStore
from memory_scope import memory_audience,summary_rows
from semantic_context import recall as semantic_recall

OWNER='768687099';STRANGER='2444309491'


class Audience(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=BehaviorStore(Path(self.tmp.name)/'behavior.sqlite')
  self.db=self.store.db;self.now=time.time();self.n=0
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def summary(self,text,scope,kind='small',origins=None,people=()):
  self.n+=1
  identifier=self.db.execute('INSERT INTO memory_summaries(scope,text,sources,updated) VALUES(?,?,?,?)',(scope,text,json.dumps(['e'+str(self.n)]),self.now+self.n)).lastrowid
  self.db.execute("INSERT INTO memory_summary_levels VALUES(?,'"  +kind+"')",(identifier,))
  for origin in (origins or [scope]):self.db.execute('INSERT INTO memory_summary_origins VALUES(?,?)',(identifier,origin))
  for person in people:self.db.execute('INSERT OR IGNORE INTO memory_summary_people VALUES(?,?)',(identifier,person))
  self.db.commit();return identifier
 def test_audited_self_big_and_foreign_small_never_reach_non_owner(self):
  """The two audited leak vectors: self/big id=52 and another chat's small id=53."""
  big=self.summary('主人私聊事务与多会话合并背景','self','big',origins=['private:'+OWNER,'group:55555','private:'+STRANGER])
  foreign_small=self.summary('主人私聊里的未解决问题','private:'+OWNER)
  own=self.summary('陌生人自己的会话摘要','private:'+STRANGER)
  stranger={item['id'] for item in self.store.reflection.rolling('private:'+STRANGER)}
  self.assertNotIn(big,stranger,'multi-origin big must not enter non-owner requests')
  self.assertNotIn(foreign_small,stranger,'another private chat small must not cross scopes')
  self.assertEqual(stranger,{own})
  for scope in ('group:55555','group:66666'):
   rolling={item['id'] for item in self.store.reflection.rolling(scope)}
   self.assertNotIn(big,rolling);self.assertNotIn(foreign_small,rolling);self.assertNotIn(own,rolling)
  self.assertEqual({item['id'] for item in self.store.reflection.rolling('self')},{big})
  self.assertEqual({item['id'] for item in self.store.reflection.rolling('private:'+OWNER,owner=OWNER)},{big,foreign_small})
  self.assertEqual({item['id'] for item in self.store.reflection.rolling('private:'+OWNER)},{foreign_small})
 def test_single_origin_big_stays_with_its_scope(self):
  big=self.summary('只来自一个群的大摘要','self','big',origins=['group:55555'])
  self.assertEqual({item['id'] for item in self.store.reflection.rolling('group:55555')},{big})
  self.assertEqual(self.store.reflection.rolling('group:66666'),[])
  self.assertEqual({item['id'] for item in self.store.reflection.rolling('self')},{big})
 def test_rolling_takes_newest_same_scope_smalls(self):
  scopes=['private:'+STRANGER]*5
  ids=[self.summary('旧摘要'+str(i),scopes[i]) for i in range(5)]
  rolling=[item['id'] for item in self.store.reflection.rolling('private:'+STRANGER)]
  self.assertEqual(rolling,list(reversed(ids))[:3],'newest three, not the oldest three')
 def test_protected_material_survives_small_quota(self):
  protected=self.summary('与陌生人还有未解决的约定','private:'+STRANGER)
  newer=[self.summary('闲聊'+str(i),'private:'+STRANGER) for i in range(4)]
  # Protection comes from evidence-backed model selection, never prose keywords.
  from summary_material import derive_claims
  claims=derive_claims('private:'+STRANGER,[{'event':'e1','sender_id':STRANGER,'user':'周六八点见','bot':'好','at':1}],{},[],{'commitments':[{'event':'e1','topic':'见面','quote':'周六八点见','who':'user','stage':'accepted'}]})
  self.db.execute('UPDATE memory_summaries SET schema_version=2,claims=? WHERE id=?',(json.dumps(claims),protected));self.db.commit()
  rolling=[item['id'] for item in self.store.reflection.rolling('private:'+STRANGER)]
  self.assertIn(protected,rolling);self.assertEqual(len(rolling),3);self.assertIn(newer[-1],rolling)
  recent_protected=self.summary('新的纠正已确认','private:'+STRANGER)
  rolling=[item['id'] for item in self.store.reflection.rolling('private:'+STRANGER)]
  self.assertIn(recent_protected,rolling);self.assertIn(protected,rolling);self.assertEqual(len(rolling),3)
 def test_recall_and_search_exclude_self_summary_for_non_owner(self):
  big=self.summary('合并背景含主人私聊内容','self','big',origins=['private:'+OWNER,'group:55555'])
  good=self.summary('平江路夜景','group:55555',people=[STRANGER])
  context={'scope':'private:'+STRANGER,'subject':STRANGER}
  self.assertTrue(memory_audience(context,{'source_scope':'group:55555'}))
  self.assertFalse(memory_audience(context,{'source_scope':'self'}))
  self.assertFalse(memory_audience(context,{'source_scope':'private:'+OWNER}))
  self.assertFalse(memory_audience({'scope':'private:'+OWNER,'subject':OWNER},{'source_scope':'private:'+STRANGER}))
  self.assertTrue(memory_audience({'scope':'private:'+OWNER,'subject':OWNER},{'source_scope':'private:'+OWNER}))
  result=self.store.reflection.recall('private:'+STRANGER,'平江路',subject=STRANGER)
  self.assertNotIn(big,{item['id'] for item in result});self.assertIn(good,{item['id'] for item in result})
  self.assertNotIn(big,[r[0] for r in summary_rows(self.db,'private:'+STRANGER,STRANGER)])
  hits=self.store.search.search('private:'+STRANGER,STRANGER,'平江路',summaries=True)
  self.assertIn(good,[item['id'] for item in hits]);self.assertNotIn(big,[item['id'] for item in hits])
 def test_semantic_context_candidates_share_the_same_gate(self):
  big=self.summary('合并背景','self','big',origins=['private:'+OWNER,'group:55555'])
  good=self.summary('木雕展览未完话题','group:55555',people=[STRANGER])
  facts,summaries=semantic_recall(self.store,'private:'+STRANGER,STRANGER,'木雕展览',{'memorySummary':True,'ownerUin':OWNER},lambda:True,{})
  self.assertNotIn(big,{item['id'] for item in summaries})
  self.assertIn(good,{item['id'] for item in summaries})
  owner_facts,owner_summaries=semantic_recall(self.store,'private:'+OWNER,OWNER,'木雕展览',{'memorySummary':True,'ownerUin':OWNER},lambda:True,{})
  self.assertIn(big,{item['id'] for item in owner_summaries},'the owner private chat may read the bot shared background')
  stranger_facts,stranger_summaries=semantic_recall(self.store,'private:'+STRANGER,STRANGER,'木雕展览',{'memorySummary':True,'ownerUin':OWNER},lambda:True,{})
  self.assertNotIn(big,{item['id'] for item in stranger_summaries})
 def test_core_note_single_scope_multi_scope_and_legacy(self):
  def note(text,scopes):
   self.db.execute('INSERT OR REPLACE INTO memory_core_notes(slot,text,scopes,people,sources,updated,schema_version,claims) VALUES(1,?,?,?,?,?,1,NULL)',(text,json.dumps(sorted(scopes)),'[]','[]',self.now));self.db.commit()
  note('私人约定',['private:'+STRANGER])
  ref=self.store.reflection
  self.assertEqual(ref.core_note('private:'+STRANGER,STRANGER)['text'],'私人约定')
  self.assertNotIn('legacy_unverified',ref.core_note('private:'+STRANGER,STRANGER))
  self.assertEqual(ref.core_note('group:55555','12345678')['text'],'')
  self.assertEqual(ref.core_note('self','0')['text'],'')
  note('跨会话背景',['group:55555','private:'+OWNER])
  self.assertEqual(ref.core_note('group:55555')['text'],'','multi-scope note stays out of contributor scopes')
  self.assertEqual(ref.core_note('self')['text'],'跨会话背景')
  self.assertEqual(ref.core_note('private:'+OWNER,OWNER,owner=OWNER)['text'],'跨会话背景')
  self.assertEqual(ref.core_note('private:'+STRANGER,STRANGER)['text'],'')
  note('旧版未拆分背景',['*'])
  legacy=ref.core_note('group:66666','12345678')
  self.assertEqual(legacy['text'],'','unknown provenance must not become public group memory')
  self.assertTrue(ref.core_note('self')['legacy_unverified'])
  self.assertEqual(ref.core_note()['text'],'旧版未拆分背景','update path and admin views still read the stored row')
 def test_person_level_group_events_still_reach_the_person(self):
  store=self.store
  store.observe('group:55555',STRANGER,'我喜欢喝红茶','g1',now=self.now,auto_capture=True)
  facts=store.observe('private:'+STRANGER,STRANGER,'我爱喝什么','g2',now=self.now+1,auto_capture=True)['facts']
  self.assertEqual(facts[0]['value'],'红茶');self.assertEqual(facts[0]['source_scope'],'group:55555')

if __name__=='__main__':unittest.main(verbosity=2)

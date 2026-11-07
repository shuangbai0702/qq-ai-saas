"""P1-2: claim evidence is findable, budgeted and deduplicated in recall views."""
import json,sqlite3,sys,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
from memory_search import MemorySearch
from memory_scope import summary_item
from semantic_context import recall as semantic_recall

PROTECTED={'id':'aabbccdd11223344','text':'周六八点在东门见，下雨改周日','kind':'interpretation',
           'speaker':{'role':'human'},'sourceRefs':[{'event':'e1','role':'user'}],
           'occurredAt':1,'status':'open','visibility':'group:70001','protected':True,
           'about':'周六东门见面'}


def claim(claim_id,text,about='x'):
    return {**PROTECTED,'id':claim_id,'text':text,'about':about,'sourceRefs':[{'event':'e1','role':'user'}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.executescript('''
        CREATE TABLE facts(scope TEXT,subject TEXT,name TEXT,value TEXT,event TEXT,updated REAL,version INTEGER,active INTEGER);
        CREATE TABLE fact_metadata(scope TEXT,subject TEXT,name TEXT,kind TEXT,origin TEXT,importance REAL,last_recalled REAL DEFAULT 0,recalls INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,name));
        CREATE TABLE memory_blocks(scope TEXT,subject TEXT,name TEXT,block_all INTEGER,rejected_values TEXT,revision INTEGER);
        CREATE TABLE memory_summary_people(summary_id INTEGER,subject TEXT,PRIMARY KEY(summary_id,subject));
        CREATE TABLE memory_summaries(id INTEGER PRIMARY KEY AUTOINCREMENT,scope TEXT,text TEXT,sources TEXT,updated REAL,schema_version INTEGER DEFAULT 1,claims TEXT);
        CREATE VIRTUAL TABLE memory_search USING fts5(kind UNINDEXED,scope UNINDEXED,subject UNINDEXED,key UNINDEXED,revision UNINDEXED,terms);
        ''')
        self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES('group:70001','闲聊总结','[]',1,2,?)",(json.dumps([PROTECTED],ensure_ascii=False),))
        self.db.commit()
        self.search=MemorySearch(self.db)


class SearchTest(Base):
    pass

    def test_claim_only_keyword_is_findable(self):
        # The keyword exists only in the claim text, never in the prose.
        rows=self.search.search('group:70001','','东门',summaries=True)
        self.assertEqual([item['id'] for item in rows],[1])
        self.assertEqual(rows[0]['protected_claims'][0]['id'],PROTECTED['id'])

    def test_claim_revision_invalidates_stale_candidate(self):
        self.search.search('group:70001','','东门',summaries=True)
        self.db.execute("UPDATE memory_summaries SET claims=? WHERE id=1",(json.dumps([claim('ffffeeee00001111','新地点是西门'),PROTECTED],ensure_ascii=False),))
        self.db.commit()
        # The old index row is gone; the new evidence re-indexes on next use.
        self.assertEqual(self.search.search('group:70001','','东门',summaries=True)[0]['protected_claims'][0]['text'],'新地点是西门')


class RecallTest(Base):
    def _store(self):
        class R:
            db=self.db;search=self.search
            memory=type('M',(),{'closed':False,'recall':lambda *a,**k:[]})()
            reflection=type('F',(),{'recall':lambda *a,**k:[],'rolling':lambda *a,**k:[]})()
        return R()

    def test_budget_counts_attached_claims_and_dedups_by_id(self):
        big=claim('bbbbcccc00001111','长'*200,'大')
        self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES('group:70001','第二条摘要','[]',2,2,?)",(json.dumps([big],ensure_ascii=False),))
        self.db.commit()
        selected,summaries=semantic_recall(self._store(),'group:70001','10001','东门',{'memorySummary':True},lambda:True,{})
        sizes=[len(json.dumps(c,ensure_ascii=False,separators=(',',':')))+len(item['summary']) for item in summaries for c in item.get('protected_claims',[])]
        self.assertTrue(sizes)
        total=sum(len(item['summary'])+sum(len(json.dumps(c,ensure_ascii=False,separators=(',',':'))) for c in item.get('protected_claims',[])) for item in summaries)
        self.assertLessEqual(total,1200+400)  # a claim may straddle the boundary once
        ids=[c['id'] for item in summaries for c in item.get('protected_claims',[])]
        self.assertEqual(len(ids),len(set(ids)))


if __name__=='__main__':
    unittest.main()

"""Summary search is retired; fixed foundation preserves structured evidence."""
import json,sqlite3,sys,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters/astrbot'))
from memory_search import MemorySearch
from memory_reflection import Reflection,summary_foundation
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
        self.reflection=Reflection(self.db)
        self.search=MemorySearch(self.db)


class SearchTest(Base):
    pass

    def test_summary_keyword_search_is_retired(self):
        self.assertEqual(self.search.search('group:70001','','东门',summaries=True),[])
        self.assertEqual(self.db.execute("SELECT count(*) FROM memory_search WHERE kind='summary'").fetchone()[0],0)
        self.assertEqual(summary_foundation(self.db,'group:70001')[0]['protected_claims'][0]['id'],PROTECTED['id'])

    def test_claim_revision_visible_without_search_index(self):
        self.search.search('group:70001','','东门',summaries=True)
        self.db.execute("UPDATE memory_summaries SET claims=? WHERE id=1",(json.dumps([claim('ffffeeee00001111','新地点是西门'),PROTECTED],ensure_ascii=False),))
        self.db.commit()
        self.assertEqual(summary_foundation(self.db,'group:70001')[0]['protected_claims'][0]['text'],'新地点是西门')


class RecallTest(Base):
    def _store(self):
        class R:
            db=self.db;search=self.search
            memory=type('M',(),{'closed':False,'recall':lambda *a,**k:[]})()
            reflection=self.reflection
        return R()

    def test_fixed_foundation_preserves_complete_summary_and_claims(self):
        big=claim('bbbbcccc00001111','长'*200,'大')
        self.db.execute("INSERT INTO memory_summaries(scope,text,sources,updated,schema_version,claims) VALUES('group:70001',?,'[]',2,2,?)",('第二条摘要'*100,json.dumps([big],ensure_ascii=False)))
        self.db.commit()
        selected,summaries=semantic_recall(self._store(),'group:70001','10001','东门',{'memorySummary':True},lambda:True,{})
        _,other=semantic_recall(self._store(),'group:70001','10001','完全不同话题',{'memorySummary':True},lambda:True,{})
        self.assertEqual(summaries,other)
        self.assertEqual(summaries,summary_foundation(self.db,'group:70001'))
        self.assertEqual(summaries[1]['summary'],'第二条摘要'*100)
        self.assertEqual(summaries[1]['protected_claims'][0]['text'],'长'*200)


if __name__=='__main__':
    unittest.main()

"""Scoped SQLite BM25 candidates, using the existing database, not a second store.

Design comparison: LivingMemory's scoped BM25 + Chinese bigrams. This small
implementation uses stdlib SQLite, the platform's tokenizer and existing facts.
The index is disposable; primary records and their versions remain authoritative.
"""
import json
from hosted_memory import tokens, ALIASES


class MemorySearch:
    def __init__(self, db):
        self.db = db
        db.executescript('''
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(
          kind UNINDEXED,scope UNINDEXED,subject UNINDEXED,key UNINDEXED,
          revision UNINDEXED,terms);
        CREATE TRIGGER IF NOT EXISTS search_fact_changed AFTER UPDATE ON facts BEGIN
          DELETE FROM memory_search WHERE kind='fact' AND scope=OLD.scope AND subject=OLD.subject AND key=OLD.name;
        END;
        CREATE TRIGGER IF NOT EXISTS search_fact_deleted AFTER DELETE ON facts BEGIN
          DELETE FROM memory_search WHERE kind='fact' AND scope=OLD.scope AND subject=OLD.subject AND key=OLD.name;
        END;
        CREATE TRIGGER IF NOT EXISTS search_summary_deleted AFTER DELETE ON memory_summaries BEGIN
          DELETE FROM memory_search WHERE kind='summary' AND key=CAST(OLD.id AS TEXT);
        END;
        CREATE TRIGGER IF NOT EXISTS search_summary_changed AFTER UPDATE ON memory_summaries BEGIN
          DELETE FROM memory_search WHERE kind='summary' AND key=CAST(OLD.id AS TEXT);
        END;''')
        db.commit()

    def search(self, scope, subject, query, summaries=False, limit=12):
        kind = 'summary' if summaries else 'fact'
        person = '' if summaries else subject
        if summaries:
            rows = self.db.execute('SELECT id,text,sources,updated FROM memory_summaries WHERE scope=?', (scope,)).fetchall()
            docs = [(str(r[0]), str(r[3]), r[1]) for r in rows]
        else:
            rows = self.db.execute('SELECT name,value,event,version,updated FROM facts WHERE scope=? AND subject=? AND active=1', (scope,subject)).fetchall()
            docs = [(r[0], str(r[3]), r[0]+' '+r[1]) for r in rows]
        old = {r[0]:str(r[1]) for r in self.db.execute('SELECT key,revision FROM memory_search WHERE kind=? AND scope=? AND subject=?',(kind,scope,person))}
        with self.db:
            for key, revision, text in docs:
                if old.get(key) == revision:continue
                self.db.execute('DELETE FROM memory_search WHERE kind=? AND scope=? AND subject=? AND key=?',(kind,scope,person,key))
                self.db.execute('INSERT INTO memory_search VALUES(?,?,?,?,?,?)',(kind,scope,person,key,revision,' '.join(sorted(tokens(text)))))
        terms = tokens(query)
        for name, aliases in ALIASES.items():
            if any(a in query for a in aliases):terms.update(tokens(name))
        if not terms:return []
        match = ' OR '.join('"'+t.replace('"','""')+'"' for t in sorted(terms)[:64])
        hits = self.db.execute('SELECT key FROM memory_search WHERE memory_search MATCH ? AND kind=? AND scope=? AND subject=? ORDER BY bm25(memory_search) LIMIT ?', (match,kind,scope,person,limit)).fetchall()
        by_key = {str(r[0]):r for r in rows}
        selected = [by_key[r[0]] for r in hits if r[0] in by_key]
        if summaries:
            return [{'id':r[0],'summary':r[1],'source_events':json.loads(r[2]),'at':r[3],'kind':'conversation_summary_not_verified_facts','recall_reason':'scoped_bm25'} for r in selected]
        return [{'name':r[0],'value':r[1],'source_event':r[2],'version':r[3],'remembered_at':r[4],'recall_reason':'scoped_bm25'} for r in selected]
